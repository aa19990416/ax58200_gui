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

def read_u16(addr):
    try:
        return struct.unpack("<H", slave._fprd(addr, 2))[0]
    except Exception:
        return None

def write_u16(addr, val):
    slave._fpwr(addr, struct.pack("<H", val))

# ---------------------------------------------------------
# 核心解法：硬體層級的免按鍵重置 (ESC PDI Reset)
# ---------------------------------------------------------
def full_hardware_soft_reset():
    print("[*] 正在向 AX58200 ESC 發送硬體復位指令 (等同按板端 Reset 鈕)...")
    try:
        # 寫入 "RES" (0x52, 0x45, 0x53) 到 ESC 暫存器 0x0041
        slave._fpwr(0x0041, b'RES')
        time.sleep(0.5)
        print("[+] 硬體級軟體復位已發出！等待從站重新開機初始化...")
        time.sleep(1.5)
    except Exception as e:
        print(f"[*] 寫入 ESC Reset: {e}")

master.config_map()

# 打包 RxPDO (14 Bytes: CW, TargetPos, TargetVel, Mode, 3-byte pad)
def pack_rxpdo(cw, pos=0, vel=0, mode=8):
    return struct.pack("<Hiib3x", cw, pos, vel, mode)

# 解包 TxPDO (14 Bytes: SW, ActPos, ActVel, ActMode, 3-byte pad)
def unpack_txpdo(data):
    if len(data) >= 14:
        return struct.unpack("<Hiib3x", data[:14])
    return 0, 0, 0, 0

current_cw = 0x0000
target_pos = 0
target_vel = 0
current_mode = 8  # 直通 Mode 8

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

# 躍遷至 OP 狀態
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
target_pos = actual_pos
print(f"[+] OP 狀態確立: 0x{read_u16(0x0130):04X} | 起始位置: {actual_pos}")

# =====================================================================
# 步驟 0: 正緣 Fault Reset 流程 (徹底符合 pAxis->PreControlword == 0)
# =====================================================================
if (last_sw & 0x0008) != 0 or (last_sw & 0x2000) != 0:
    print(f"[*] 檢測到殘留報警 (SW=0x{last_sw:04X})，執行嚴格正緣清除...")
    # 必須先送 0 讓 PreControlword 確立為 0
    current_cw = 0x0000
    time.sleep(0.2)
    # 送 0x0080 產生 0 -> 1 正緣觸發 MC_FaultReset()
    current_cw = 0x0080
    time.sleep(0.3)
    # 再次降為 0
    current_cw = 0x0000
    time.sleep(0.2)
    
    if (last_sw & 0x0008) != 0:
        print(f"[-] 軟體 CiA402 復位無效 (SW=0x{last_sw:04X})，觸發硬體級重置...")
        full_hardware_soft_reset()

# =====================================================================
# 步驟 1: 乾淨使能 (Mode 8)
# =====================================================================
print("\n" + "=" * 65)
print("[步驟 1] 激磁使能 (Mode 8 CSP)...")
print("=" * 65)

current_cw = 0x0006
target_pos = actual_pos
time.sleep(0.2)

current_cw = 0x0007
time.sleep(0.1)

current_cw = 0x000F
target_pos = actual_pos
print("[*] 正在充磁與激磁鎖軸，等待 1.0 秒...")
time.sleep(1.0)
target_pos = actual_pos

print(f"[+] 使能完成！SW = 0x{last_sw:04X} | 模式: {actual_mode} | 鎖定基準: {actual_pos}")

# =====================================================================
# 步驟 2: 運動驗證 (同時帶 Target Position 與 前饋 Target Velocity)
# =====================================================================
print("\n" + "=" * 65)
print("[步驟 2] 正向平滑運動測試 (+300 counts，帶前饋速度)...")
print("=" * 65)

def smooth_move(delta_counts, duration=2.5):
    global target_pos, target_vel
    start_p = target_pos
    steps = int(duration / 0.001)

    for i in range(steps + 1):
        s = i / float(steps)
        scale = 10 * (s ** 3) - 15 * (s ** 4) + 6 * (s ** 5)
        vel_scale = (30 * (s ** 2) - 60 * (s ** 3) + 30 * (s ** 4)) / duration
        
        target_pos = int(start_p + delta_counts * scale)
        target_vel = int(delta_counts * vel_scale)

        diff = target_pos - actual_pos
        sys.stdout.write(f"\r  [CSP運動] 目標: {target_pos:6d} | 實際: {actual_pos:6d} | 誤差: {diff:4d} | 反饋速度: {actual_vel:4d}")
        sys.stdout.flush()
        time.sleep(0.001)
        
    target_vel = 0
    print()

try:
    print("[*] 1. 正向旋轉 +300 counts...")
    smooth_move(300, duration=2.5)
    time.sleep(0.5)

    print("[*] 2. 返回原點 -300 counts...")
    smooth_move(-300, duration=2.5)
    time.sleep(0.5)

    print("\n[+] 運動完成！保持鎖軸 2 秒...")
    time.sleep(2.0)

except KeyboardInterrupt:
    print("\n[*] 使用者中斷。")

finally:
    target_vel = 0
    # 退出前嚴格正緣清零，絕不留殘留 Fault
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
