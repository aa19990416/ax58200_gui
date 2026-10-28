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

master.config_map()

# 打包 RxPDO (14 Bytes: CW, TargetPos, TargetVel, Mode, 3-byte pad)
# 模式固定設為 9 (CSV)
def pack_rxpdo(cw, pos=0, vel=0, mode=9):
    return struct.pack("<Hiib3x", cw, pos, vel, mode)

# 解包 TxPDO (14 Bytes: SW, ActPos, ActVel, ActMode, 3-byte pad)
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

def read_u16(addr):
    try:
        return struct.unpack("<H", slave._fprd(addr, 2))[0]
    except Exception:
        return None

def write_u16(addr, val):
    slave._fpwr(addr, struct.pack("<H", val))

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
print(f"[+] OP 狀態確認: 0x{read_u16(0x0130):04X} | 起始位置: {actual_pos}")

# =====================================================================
# 步驟 0: 自動 Fault Reset 清除
# =====================================================================
print("\n" + "=" * 65)
print("[步驟 0] 檢查並自動復位錯誤狀態...")
print("=" * 65)

# 先下發 0x0000
current_cw = 0x0000
time.sleep(0.2)

if (last_sw & 0x0008) != 0 or (last_sw & 0x2000) != 0:
    print(f"[*] 檢測到 Fault (SW=0x{last_sw:04X})，下發 0x0080 正緣復位...")
    current_cw = 0x0080
    time.sleep(0.3)
    current_cw = 0x0000
    time.sleep(0.2)
    print(f"[*] 復位後 SW: 0x{last_sw:04X}")

# =====================================================================
# 步驟 1: 原廠 Servo-ON (使能進入 CSV 模式)
# =====================================================================
print("\n" + "=" * 65)
print("[步驟 1] 原廠 Servo-ON 使能 (Mode 9: CSV)...")
print("=" * 65)

current_cw = 0x0006
time.sleep(0.2)

current_cw = 0x0007
time.sleep(0.1)

current_cw = 0x000F
time.sleep(0.3)

print("[*] 正在通電充磁吸合，靜置 1.0 秒...")
time.sleep(1.0)

print(f"[+] 使能完成！SW = 0x{last_sw:04X} | 當前反饋模式: {actual_mode} | 當前位置: {actual_pos}")

if (last_sw & 0x000F) == 0x0008:
    print(f"[-] 伺服仍處於 Fault 狀態 (SW=0x{last_sw:04X})，請確認驅動板電源！")
    current_cw = 0x0000
    running = False
    th.join()
    exit()

# =====================================================================
# 步驟 2: 原廠 CSV 連續平滑旋轉測試 (正轉 -> 減速 -> 反轉 -> 停機)
# =====================================================================
print("\n" + "=" * 65)
print("[步驟 2] CSV 速度模式運轉測試 (仿原廠面板，轉速 0 -> 400 rpm)...")
print("=" * 65)

try:
    # 1. 平滑正向加速至 400 rpm
    print("[*] 1. 正向平滑加速 (0 -> 400 rpm)...")
    for v in range(0, 401, 10):
        target_vel = v
        sys.stdout.write(f"\r  [CSV 正轉] 指令轉速: {target_vel:4d} rpm | 實際轉速: {actual_vel:4d} rpm | 實際位置: {actual_pos:7d}")
        sys.stdout.flush()
        time.sleep(0.04)
    print()

    # 恆速維持 2.5 秒
    print("[*] 恆速運轉中 (維持 400 rpm 運轉 2.5 秒)...")
    t_end = time.time() + 2.5
    while time.time() < t_end:
        sys.stdout.write(f"\r  [CSV 恆速] 指令轉速: {target_vel:4d} rpm | 實際轉速: {actual_vel:4d} rpm | 實際位置: {actual_pos:7d}")
        sys.stdout.flush()
        time.sleep(0.05)
    print()

    # 減速至 0
    print("[*] 2. 平滑減速至 0...")
    for v in range(400, -1, -10):
        target_vel = v
        sys.stdout.write(f"\r  [CSV 減速] 指令轉速: {target_vel:4d} rpm | 實際轉速: {actual_vel:4d} rpm | 實際位置: {actual_pos:7d}")
        sys.stdout.flush()
        time.sleep(0.03)
    print()
    time.sleep(0.5)

    # 3. 平滑反向加速至 -400 rpm
    print("[*] 3. 反向平滑加速 (0 -> -400 rpm)...")
    for v in range(0, -401, -10):
        target_vel = v
        sys.stdout.write(f"\r  [CSV 反轉] 指令轉速: {target_vel:4d} rpm | 實際轉速: {actual_vel:4d} rpm | 實際位置: {actual_pos:7d}")
        sys.stdout.flush()
        time.sleep(0.04)
    print()

    # 恆速維持 2.5 秒
    print("[*] 恆速反轉中 (維持 -400 rpm 運轉 2.5 秒)...")
    t_end = time.time() + 2.5
    while time.time() < t_end:
        sys.stdout.write(f"\r  [CSV 恆速] 指令轉速: {target_vel:4d} rpm | 實際轉速: {actual_vel:4d} rpm | 實際位置: {actual_pos:7d}")
        sys.stdout.flush()
        time.sleep(0.05)
    print()

    # 減速回 0
    print("[*] 4. 平滑減速停機...")
    for v in range(-400, 1, 10):
        target_vel = v
        sys.stdout.write(f"\r  [CSV 停機] 指令轉速: {target_vel:4d} rpm | 實際轉速: {actual_vel:4d} rpm | 實際位置: {actual_pos:7d}")
        sys.stdout.flush()
        time.sleep(0.03)
    print()

    print("\n[+] CSV 正反轉連續運轉測試全部完成！")

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
