import pysoem
import struct
import time
import math
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

def pack_rxpdo(cw, pos=0, vel=0, mode=6):
    return struct.pack("<Hiib3x", cw, pos, vel, mode)

def unpack_txpdo(data):
    if len(data) >= 14:
        return struct.unpack("<Hiib3x", data[:14])
    return 0, 0, 0, 0

current_cw = 0x0000
target_pos = 0
target_vel = 0
current_mode = 6

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

        elapsed = time.perf_counter() - t0
        delay = 0.001 - elapsed
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

# 推進至 OP 狀態
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
print(f"[+] 從站 OP 狀態確認: 0x{read_u16(0x0130):04X} | 起始位置: {actual_pos}")

# 步驟 1: 以 Mode 6 進入 Servo-On
print("\n" + "=" * 65)
print("[步驟 1] 依序執行 Servo-On (Homing Mode 6)...")
print("=" * 65)

for cw, expect in [(0x0006, 0x0021), (0x0007, 0x0023), (0x000F, 0x0027)]:
    current_cw = cw
    target_pos = actual_pos
    t_wait = time.time() + 1.5
    while time.time() < t_wait:
        if (last_sw & 0x006F) == expect:
            break
        time.sleep(0.02)

if (last_sw & 0x006F) != 0x0027:
    print(f"[-] Servo-On 失敗，SW: 0x{last_sw:04X}")
    running = False
    th.join()
    exit()

print(f"[+] Servo-On 成功！SW = 0x{last_sw:04X}")

# 步驟 2: 觸發 Homing
print("\n" + "=" * 65)
print("[步驟 2] 觸發 Homing 回原點標定...")
print("=" * 65)

current_cw = 0x001F
t_start = time.time()
homing_ok = False
while time.time() - t_start < 8.0:
    is_attained = bool(last_sw & 0x1000)
    is_error = bool(last_sw & 0x2000)
    sys.stdout.write(f"\r  [Homing 中] SW: 0x{last_sw:04X} | 位置: {actual_pos:8d} | Homing 完成: {is_attained}")
    sys.stdout.flush()
    if is_attained:
        homing_ok = True
        break
    if is_error:
        print(f"\n[-] Homing 報錯，SW: 0x{last_sw:04X}")
        break
    time.sleep(0.05)
print()

if not homing_ok:
    print("[-] Homing 失敗，中斷後續操作。")
    running = False
    th.join()
    exit()

# 步驟 3: 降使能並切換至 CSP 模式 (Mode 8)
print("\n" + "=" * 65)
print("[步驟 3] 降使能 -> 切換模式為 CSP (Mode 8) -> 重新激磁...")
print("=" * 65)

# A. 降回 Shutdown (0x0006)
current_cw = 0x0006
time.sleep(0.2)

# B. 修改模式為 8
current_mode = 8
target_pos = actual_pos
time.sleep(0.2)

# 等待 0x6061 反饋變更為 8
t_wait_m = time.time() + 2.0
while time.time() < t_wait_m:
    if actual_mode == 8:
        break
    time.sleep(0.05)
print(f"[+] 模式切換狀態 -> 反饋模式 (0x6061): {actual_mode} ({'已成功轉為 CSP 模式！' if actual_mode == 8 else '仍未切換'})")

# C. 重新切入 0x0007 -> 0x000F 鎖軸
target_pos = actual_pos
current_cw = 0x0007
time.sleep(0.1)
current_cw = 0x000F
time.sleep(0.2)
print(f"[+] 重新使能完成！SW = 0x{last_sw:04X} | 模式: {actual_mode} | 當前位置: {actual_pos}")

# 步驟 4: CSP 平滑運動驗證
def move_csp(target, duration):
    global target_pos
    start = target_pos
    steps = int(duration / 0.005)
    for i in range(steps + 1):
        s = i / steps
        scale = 10 * (s ** 3) - 15 * (s ** 4) + 6 * (s ** 5)
        target_pos = int(start + (target - start) * scale)
        diff = target_pos - actual_pos
        sys.stdout.write(f"\r  [CSP 運動] 目標: {target_pos:7d} | 實際: {actual_pos:7d} | 誤差: {diff:4d} | 速度: {actual_vel:5d}")
        sys.stdout.flush()
        time.sleep(0.005)
    print()

try:
    print("\n" + "=" * 65)
    print("[步驟 4] 開始執行 CSP 運動追隨驗證...")
    print("=" * 65)

    base = actual_pos
    print("[*] 1. 正轉 +2000 counts...")
    move_csp(base + 2000, 1.5)
    time.sleep(0.5)

    print("[*] 2. 返回原點...")
    move_csp(base, 1.5)
    time.sleep(0.5)

    print("[*] 3. 正轉一整圈 (+10000 counts)...")
    move_csp(base + 10000, 2.5)
    time.sleep(0.5)

    print("[*] 4. 反轉回原點...")
    move_csp(base, 2.5)
    print("\n[+] 運動驗證完成！")

except KeyboardInterrupt:
    print("\n[*] 使用者中斷測試。")

finally:
    print("[*] 正在退使能並關閉 EtherCAT 連線...")
    current_cw = 0x0000
    time.sleep(0.2)
    running = False
    th.join()
    slave.output = bytes(len(slave.output))
    master.send_processdata()
    master.close()
    print("[*] 伺服已安全斷電釋放。")
