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

slave.state = pysoem.PREOP_STATE
master.write_state()
master.state_check(pysoem.PREOP_STATE, 50000)
time.sleep(0.1)

def sdo_w(idx, sub, val, fmt):
    try:
        slave.sdo_write(idx, sub, struct.pack(fmt, val))
        return True
    except Exception:
        return False

def sdo_r(idx, sub, fmt):
    try:
        return struct.unpack(fmt, slave.sdo_read(idx, sub))[0]
    except Exception:
        return None

print("\n" + "=" * 65)
print("1. 檢查與配置 CSP 核心運動限制與週期物件")
print("=" * 65)

# 配置 0x60C2 插補週期為 1ms (1 * 10^-3)
sdo_w(0x60C2, 1, 1, "<B")
sdo_w(0x60C2, 2, -3, "<b")
c2_val = sdo_r(0x60C2, 1, "<B")
c2_idx = sdo_r(0x60C2, 2, "<b")
print(f"  0x60C2:01/02 (Interpolation Cycle) : {c2_val} * 10^({c2_idx}) s")

# 檢查與配置最大扭矩 0x6072 (通常 1000 代表 100.0%)
tq_max = sdo_r(0x6072, 0, "<H")
print(f"  0x6072:00 (Max Torque)             : {tq_max}")
if tq_max == 0 or tq_max is None:
    sdo_w(0x6072, 0, 1000, "<H")
    print(f"    └─ 已重寫 0x6072 = 1000 (100% 額定扭矩)")

# 檢查 0x6080 (Max Motor Speed)
spd_max = sdo_r(0x6080, 0, "<I")
print(f"  0x6080:00 (Max Motor Speed)        : {spd_max}")
if spd_max == 0 or spd_max is None:
    sdo_w(0x6080, 0, 6000, "<I")

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

        elapsed = time.perf_counter() - t0
        delay = 0.001 - elapsed
        if delay > 0:
            time.sleep(delay)

th = threading.Thread(target=pdo_loop, daemon=True)
th.start()

# 躍遷至 OP
slave._fpwr(0x0120, struct.pack("<H", 0x0004))
time.sleep(0.1)
slave._fpwr(0x0120, struct.pack("<H", 0x0008))
time.sleep(0.2)

time.sleep(0.1)
target_pos = actual_pos

print("\n" + "=" * 65)
print("2. 執行官方流程: Servo-On -> Homing -> CSP (Mode 8)")
print("=" * 65)

# Servo-On
for cw in [0x0006, 0x0007, 0x000F]:
    current_cw = cw
    target_pos = actual_pos
    time.sleep(0.2)

# Homing
current_cw = 0x001F
t_end = time.time() + 6.0
while time.time() < t_end:
    if last_sw & 0x1000:
        break
    time.sleep(0.05)
print(f"[+] Homing 完成！SW = 0x{last_sw:04X} | 原點基準: {actual_pos}")

# 降使能換模式
current_cw = 0x0006
time.sleep(0.1)
current_mode = 8
target_pos = actual_pos
time.sleep(0.2)

# 重新激磁進入 CSP
current_cw = 0x0007
time.sleep(0.1)
current_cw = 0x000F
time.sleep(0.2)

print(f"[+] 進入 CSP 完成！SW = 0x{last_sw:04X} | 模式: {actual_mode} | 當前位置: {actual_pos}")

# 運動測試
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
    print("3. CSP 運動驗證 (若仍無動靜，請嘗試用手稍微扳動軸心感受是否有阻力)")
    print("=" * 65)

    base = actual_pos
    print("[*] 1. 正轉 +2000 counts...")
    move_csp(base + 2000, 1.5)
    time.sleep(0.5)

    print("[*] 2. 返回原點...")
    move_csp(base, 1.5)

except KeyboardInterrupt:
    pass

finally:
    current_cw = 0x0000
    time.sleep(0.2)
    running = False
    th.join()
    slave.output = bytes(len(slave.output))
    master.send_processdata()
    master.close()
    print("\n[*] 測試結束，連線已安全斷開。")
