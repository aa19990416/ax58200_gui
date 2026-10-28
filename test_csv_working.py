import pysoem
import struct
import time
import threading
import sys

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到 EtherCAT 從站！")
    exit()

slave = master.slaves[0]
print(f"[+] 找到從站: {slave.name}")

# 在 PREOP 讀取當前是否有殘留 Error Code
slave.state = pysoem.PREOP_STATE
master.write_state()
master.state_check(pysoem.PREOP_STATE, 50000)
time.sleep(0.1)

try:
    err_code = struct.unpack("<H", slave.sdo_read(0x603F, 0))[0]
    print(f"[*] 讀取 0x603F 錯誤碼: 0x{err_code:04X}")
except Exception as e:
    print(f"[*] 讀取 0x603F 提示: {e}")

master.config_map()

# 打包 RxPDO: CW, TargetPos, TargetVel, Mode, 3-byte pad
def pack_rxpdo(cw, pos=0, vel=0, mode=9):
    return struct.pack("<Hiib3x", cw, pos, vel, mode)

def unpack_txpdo(data):
    if len(data) >= 14:
        return struct.unpack("<Hiib3x", data[:14])
    return 0, 0, 0, 0

current_cw = 0x0000
target_pos = 0
target_vel = 0
current_mode = 9  # 固定 Mode 9 (CSV)

last_sw = 0
actual_pos = 0
actual_vel = 0
actual_mode = 0
running = True

def pdo_loop():
    global last_sw, actual_pos, actual_vel, actual_mode, running
    while running:
        t0 = time.perf_counter()
        slave.output = pack_rxpdo(current_cw, target_pos, target_vel, current_mode)
        master.send_processdata()
        wkc = master.receive_processdata(2000)

        if wkc > 0 and slave.input:
            sw, pos, vel, mode = unpack_txpdo(slave.input)
            last_sw = sw
            actual_pos = pos
            actual_vel = vel
            actual_mode = mode

        delay = 0.001 - (time.perf_counter() - t0)
        if delay > 0:
            time.sleep(delay)

th = threading.Thread(target=pdo_loop, daemon=True)
th.start()

def read_u16(addr):
    try:
        return struct.unpack("<H", slave._fprd(addr, 2))[0]
    except Exception:
        return None

def write_u16(addr, val):
    slave._fpwr(addr, struct.pack("<H", val))

slave.state = pysoem.SAFEOP_STATE
master.write_state()
master.state_check(pysoem.SAFEOP_STATE, 50000)
time.sleep(0.1)

cur_stat = read_u16(0x0130)
if cur_stat and (cur_stat & 0x0010):
    write_u16(0x0120, 0x0014)
    time.sleep(0.1)
write_u16(0x0120, 0x0004)
time.sleep(0.1)
write_u16(0x0120, 0x0008)
time.sleep(0.2)

time.sleep(0.1)
print(f"[+] OP 狀態確認: 0x{read_u16(0x0130):04X} | 起始位置: {actual_pos}")

# =====================================================================
# 步驟 0: 正式可靠的軟體 Fault Reset (徹底解決 0x1208)
# =====================================================================
print("\n" + "=" * 65)
print("[步驟 0] 檢查並可靠清除 Fault 狀態 (免手動按鍵)...")
print("=" * 65)

# 先送 0x0000 確保 PreControlword = 0，並讓馬達切入 STOP
current_cw = 0x0000
time.sleep(0.3)

if (last_sw & 0x0008) != 0:
    print(f"[*] 當前 SW = 0x{last_sw:04X} (處於 Fault)，執行脈衝復位...")
    # 持續下發 0x0080 至少 500ms 讓從站狀態機完成 Transition 15
    current_cw = 0x0080
    t_rst = time.time()
    while time.time() - t_rst < 0.6:
        if (last_sw & 0x0008) == 0:
            break
        time.sleep(0.02)
    
    current_cw = 0x0000
    time.sleep(0.2)
    print(f"[+] 復位後 SW = 0x{last_sw:04X}")

# =====================================================================
# 步驟 1: 順序使能 + 模式重新掛載 (解決速度環不啟動問題)
# =====================================================================
print("\n" + "=" * 65)
print("[步驟 1] 激磁使能並掛載 CSV 速度控制環...")
print("=" * 65)

# A. Ready to switch on
current_cw = 0x0006
time.sleep(0.2)

# B. Switched on
current_cw = 0x0007
time.sleep(0.1)

# C. Operation Enabled
current_cw = 0x000F
print("[*] 通電充磁對齊中，靜置 1.2 秒...")
time.sleep(1.2)

# D. 關鍵修復：在使能後做一次微幅跳躍觸發 CIA402CSV_Init 重新確認模式
current_cw = 0x0007
time.sleep(0.05)
current_cw = 0x000F
time.sleep(0.2)

print(f"[+] 使能就緒！SW = 0x{last_sw:04X} | 模式: {actual_mode} | 當前物理位置: {actual_pos}")

# =====================================================================
# 步驟 2: CSV 速度運轉測試 (以 500 為單位平滑加速)
# =====================================================================
print("\n" + "=" * 65)
print("[步驟 2] CSV 速度模式旋轉測試 (Target Velocity: 0 -> 500)...")
print("=" * 65)

try:
    print("[*] 1. 正向平滑加速 (0 -> 500)...")
    for v in range(0, 501, 20):
        target_vel = v
        sys.stdout.write(f"\r  [CSV 正轉] 指令速度: {target_vel:4d} | 實際轉速: {actual_vel:4d} | 實際位置: {actual_pos:7d}")
        sys.stdout.flush()
        time.sleep(0.05)
    print()

    print("[*] 維持 500 運轉 3 秒...")
    t_end = time.time() + 3.0
    while time.time() < t_end:
        sys.stdout.write(f"\r  [CSV 恆速] 指令速度: {target_vel:4d} | 實際轉速: {actual_vel:4d} | 實際位置: {actual_pos:7d}")
        sys.stdout.flush()
        time.sleep(0.05)
    print()

    print("[*] 2. 減速停機...")
    for v in range(500, -1, -20):
        target_vel = v
        sys.stdout.write(f"\r  [CSV 減速] 指令速度: {target_vel:4d} | 實際轉速: {actual_vel:4d} | 實際位置: {actual_pos:7d}")
        sys.stdout.flush()
        time.sleep(0.03)
    print()

    print("\n[+] 運轉測試完畢！")

except KeyboardInterrupt:
    print("\n[*] 使用者中斷。")

finally:
    target_vel = 0
    current_cw = 0x0000
    time.sleep(0.1)
    current_cw = 0x0080
    time.sleep(0.1)
    current_cw = 0x0000
    time.sleep(0.1)
    running = False
    th.join()
    slave.output = bytes(len(slave.output))
    master.send_processdata()
    master.close()
    print("[+] 伺服斷電釋放完成。")
