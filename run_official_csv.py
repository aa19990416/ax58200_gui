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

# 嚴格保持乾淨：不寫任何 SDO，直接自動配置 PDO
master.config_map()

# RxPDO 打包: (CW, TargetPos, TargetVel, Mode, pad)
def pack_rxpdo(cw, pos=0, vel=0, mode=9):
    return struct.pack("<Hiib3x", cw, pos, vel, mode)

# TxPDO 解包: (SW, ActPos, ActVel, ActMode, pad)
def unpack_txpdo(data):
    if len(data) >= 14:
        return struct.unpack("<Hiib3x", data[:14])
    return 0, 0, 0, 0

current_cw = 0x0000
target_pos = 0
target_vel = 0
current_mode = 9  # 官方展示模式: Mode 9 (CSV 速度模式)

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

print("\n" + "=" * 65)
print("[原廠步驟 1] 執行 Servo-ON 使能激磁 (Mode 9: CSV)...")
print("=" * 65)

# 清除 Fault
if (last_sw & 0x0008) != 0:
    current_cw = 0x0080
    time.sleep(0.2)
    current_cw = 0x0000
    time.sleep(0.1)

# 依序進入使能態
current_cw = 0x0006
time.sleep(0.2)

current_cw = 0x0007
time.sleep(0.1)

current_cw = 0x000F
time.sleep(0.3)

print(f"[+] 使能確認: SW = 0x{last_sw:04X} | 模式: {actual_mode} | 當前位置: {actual_pos}")

if (last_sw & 0x006F) == 0x0027:
    print("[!!! 激磁鎖軸成功 !!!] 電磁力已建立，伺服處於 CSV 模式！\n")
else:
    print("[-] 使能未完成，請觀察 SW 數值。")
    current_cw = 0x0000
    running = False
    th.join()
    exit()

print("=" * 65)
print("[原廠步驟 2] 按下 CSV ON 開關，啟動速度連續旋轉...")
print("=" * 65)

try:
    # 測試 1: 緩慢加速至 300 rpm，持續旋轉 3 秒
    print("[*] 1. 平滑加速旋轉 (Target Velocity: 0 -> 300 rpm)...")
    for v in range(0, 301, 10):
        target_vel = v
        sys.stdout.write(f"\r  [CSV 運轉] 指令轉速: {target_vel:4d} | 實際轉速: {actual_vel:4d} | 當前位置: {actual_pos:7d}")
        sys.stdout.flush()
        time.sleep(0.03)
    print()

    print("[*] 恆速運轉中 (維持 300 rpm 運轉 3 秒)...")
    t_end = time.time() + 3.0
    while time.time() < t_end:
        sys.stdout.write(f"\r  [CSV 運轉] 指令轉速: {target_vel:4d} | 實際轉速: {actual_vel:4d} | 當前位置: {actual_pos:7d}")
        sys.stdout.flush()
        time.sleep(0.05)
    print()

    # 平滑減速停機
    print("[*] 2. 平滑減速停機 (300 -> 0 rpm)...")
    for v in range(300, -1, -10):
        target_vel = v
        sys.stdout.write(f"\r  [CSV 減速] 指令轉速: {target_vel:4d} | 實際轉速: {actual_vel:4d} | 當前位置: {actual_pos:7d}")
        sys.stdout.flush()
        time.sleep(0.03)
    print()
    print("\n[+] 速度模式連續旋轉測試完成！")

except KeyboardInterrupt:
    print("\n[*] 使用者中斷。")

finally:
    target_vel = 0
    current_cw = 0x0000
    time.sleep(0.2)
    running = False
    th.join()
    slave.output = bytes(len(slave.output))
    master.send_processdata()
    master.close()
    print("[*] 伺服已安全斷電釋放。")
