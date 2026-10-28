import pysoem
import struct
import time
import threading
import sys

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到 EtherCAT 從站！")
    sys.exit(1)

slave = master.slaves[0]
print(f"[+] 找到從站: {slave.name}")

def read_u16(addr):
    try:
        return struct.unpack("<H", slave._fprd(addr, 2))[0]
    except Exception:
        return None

def write_u16(addr, val):
    slave._fpwr(addr, struct.pack("<H", val))

# -------------------------------------------------------------
# 核心解法 1: 軟體層徹底解除 0x0012 AL Error 鎖死 (免手按 Reset 鈕)
# -------------------------------------------------------------
cur_al = read_u16(0x0130)
if cur_al and (cur_al & 0x0010): # 檢查是否有 Error 標記
    print(f"[*] 偵測到從站處於 AL Error (0x{cur_al:04X})，執行軟體 Error Acknowledge...")
    # 寫入 (當前狀態 | 0x0010) 來確認消除錯誤
    write_u16(0x0120, (cur_al & 0x000F) | 0x0010)
    time.sleep(0.1)
    # 請求乾淨退回 PREOP
    write_u16(0x0120, 0x0002)
    time.sleep(0.2)
    print(f"[+] 錯誤已清除！當前 AL Status: 0x{read_u16(0x0130):04X}")

# 映射 PDO
master.config_map()

# -------------------------------------------------------------
# 核心解法 2: 依照官方手冊配置 1ms Distributed Clocks (DC)
# -------------------------------------------------------------
try:
    slave.dc_sync(True, 1000000) # 1ms (1,000,000 ns) Sync0 週期
    print("[+] 成功啟用 1ms Distributed Clocks (DC 同步)！")
except Exception as e:
    print(f"[*] DC 配置提示: {e}")

def pack_rxpdo(cw, pos=0, vel=0, mode=9):
    return struct.pack("<Hiib3x", cw, pos, vel, mode)

def unpack_txpdo(data):
    if len(data) >= 14:
        return struct.unpack("<Hiib3x", data[:14])
    return 0, 0, 0, 0

current_cw = 0x0000
target_pos = 0
target_vel = 0
current_mode = 9  # 官方展示 CSV 模式

last_sw = 0
actual_pos = 0
actual_vel = 0
actual_mode = 0
last_wkc = 0
running = True

def pdo_loop():
    global last_sw, actual_pos, actual_vel, actual_mode, last_wkc, running
    while running:
        t0 = time.perf_counter()
        slave.output = pack_rxpdo(current_cw, target_pos, target_vel, current_mode)
        master.send_processdata()
        wkc = master.receive_processdata(2000)
        last_wkc = wkc

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

# 躍遷至 OP 狀態
slave.state = pysoem.SAFEOP_STATE
master.write_state()
master.state_check(pysoem.SAFEOP_STATE, 50000)
time.sleep(0.1)

slave.state = pysoem.OP_STATE
master.write_state()
master.state_check(pysoem.OP_STATE, 50000)
time.sleep(0.2)

al_stat = read_u16(0x0130)
if al_stat != 0x0008:
    write_u16(0x0120, 0x0008)
    time.sleep(0.2)
    al_stat = read_u16(0x0130)

print(f"[+] OP 狀態確認: 0x{al_stat:04X} | 週期資料 WKC: {last_wkc}")

# 等待接收第一筆有效 PDO
t0 = time.time()
while time.time() - t0 < 3.0:
    if last_wkc > 0 and last_sw != 0:
        break
    time.sleep(0.05)

print(f"[+] 當前狀態: SW = 0x{last_sw:04X} | 模式: {actual_mode} | 起始位置: {actual_pos}")

# =============================================================
# 步驟 0: 自動 Fault Reset
# =============================================================
if (last_sw & 0x0008) != 0:
    print(f"[*] 檢測到 Fault (SW=0x{last_sw:04X})，下發 0x0080 正緣復位...")
    current_cw = 0x0000
    time.sleep(0.2)
    current_cw = 0x0080
    time.sleep(0.3)
    current_cw = 0x0000
    time.sleep(0.2)
    print(f"[*] 復位後 SW = 0x{last_sw:04X}")

# =============================================================
# 步驟 1: 原廠 Servo-ON (使能激磁)
# =============================================================
print("\n" + "=" * 65)
print("[步驟 1] 原廠 Servo-ON 使能 (Mode 9: CSV)...")
print("=" * 65)

current_cw = 0x0006
time.sleep(0.2)

current_cw = 0x0007
time.sleep(0.1)

current_cw = 0x000F
print("[*] 正在通電充磁吸合，靜置 1.0 秒...")
time.sleep(1.0)

print(f"[+] 使能完成！SW = 0x{last_sw:04X} | 當前反饋模式: {actual_mode} | 當前位置: {actual_pos}")

# =============================================================
# 步驟 2: 原廠 CSV 連續平滑旋轉測試 (轉速 0 -> 400 rpm)
# =============================================================
print("\n" + "=" * 65)
print("[步驟 2] CSV 速度模式運轉測試 (指令轉速 0 -> 400 rpm)...")
print("=" * 65)

try:
    print("[*] 1. 正向平滑加速 (0 -> 400 rpm)...")
    for v in range(0, 401, 10):
        target_vel = v
        sys.stdout.write(f"\r  [CSV 正轉] 指令轉速: {target_vel:4d} rpm | 實際轉速: {actual_vel:4d} rpm | 實際位置: {actual_pos:7d}")
        sys.stdout.flush()
        time.sleep(0.04)
    print()

    print("[*] 維持 400 rpm 運轉 3 秒...")
    t_end = time.time() + 3.0
    while time.time() < t_end:
        sys.stdout.write(f"\r  [CSV 恆速] 指令轉速: {target_vel:4d} rpm | 實際轉速: {actual_vel:4d} rpm | 實際位置: {actual_pos:7d}")
        sys.stdout.flush()
        time.sleep(0.05)
    print()

    print("[*] 2. 平滑減速停機...")
    for v in range(400, -1, -10):
        target_vel = v
        sys.stdout.write(f"\r  [CSV 減速] 指令轉速: {target_vel:4d} rpm | 實際轉速: {actual_vel:4d} rpm | 實際位置: {actual_pos:7d}")
        sys.stdout.flush()
        time.sleep(0.03)
    print()

    print("\n[+] 運轉測試完成！")

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
