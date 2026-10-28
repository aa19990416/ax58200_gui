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

# 軌跡插補變數 (由 1ms 迴圈直接計算，保證完全平滑)
active_motion = False
motion_start_pos = 0
motion_target_pos = 0
motion_start_time = 0
motion_duration = 1.0

def pdo_loop():
    global last_sw, actual_pos, actual_vel, actual_mode, last_wkc, running
    global target_pos, active_motion
    while running:
        t0 = time.perf_counter()
        
        # 1ms 級別平滑軌跡生成器 (5次多項式)
        if active_motion:
            now = time.perf_counter()
            elapsed = now - motion_start_time
            if elapsed >= motion_duration:
                target_pos = motion_target_pos
                active_motion = False
            else:
                s = elapsed / motion_duration
                scale = 10 * (s ** 3) - 15 * (s ** 4) + 6 * (s ** 5)
                target_pos = int(motion_start_pos + (motion_target_pos - motion_start_pos) * scale)
        
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

# 進入 OP
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
print(f"[+] OP 狀態確認: 0x{read_u16(0x0130):04X} | 起始位置: {actual_pos}")

# 步驟 1: Homing 模式 Servo-On
for cw in [0x0006, 0x0007, 0x000F]:
    current_cw = cw
    target_pos = actual_pos
    time.sleep(0.2)

# 步驟 2: Homing 回原點
current_cw = 0x001F
t_end = time.time() + 6.0
while time.time() < t_end:
    if last_sw & 0x1000:
        break
    time.sleep(0.05)
print(f"[+] Homing 完成！SW = 0x{last_sw:04X} | 原點基準: {actual_pos}")

# 步驟 3: 降使能換模式至 CSP (Mode 8)
current_cw = 0x0006
time.sleep(0.15)
current_mode = 8
target_pos = actual_pos
time.sleep(0.15)

# 等待 0x6061 變更為 8
t_m = time.time() + 1.5
while time.time() < t_m:
    if actual_mode == 8:
        break
    time.sleep(0.02)

# 重新激磁鎖軸
current_cw = 0x0007
time.sleep(0.1)
current_cw = 0x000F
time.sleep(0.2)

print(f"[+] 激磁完成！SW = 0x{last_sw:04X} | 模式: {actual_mode} | 當前位置: {actual_pos}")

def execute_move(dest, dur=2.0):
    global active_motion, motion_start_pos, motion_target_pos, motion_start_time, motion_duration
    motion_start_pos = target_pos
    motion_target_pos = dest
    motion_duration = dur
    motion_start_time = time.perf_counter()
    active_motion = True
    
    while active_motion:
        diff = target_pos - actual_pos
        sys.stdout.write(f"\r  [1ms插補運動] 目標: {target_pos:6d} | 實際: {actual_pos:6d} | 誤差: {diff:5d} | 速度: {actual_vel:4d}")
        sys.stdout.flush()
        time.sleep(0.02)
    print()

try:
    base = actual_pos
    print("\n" + "=" * 65)
    print("測試 1: 微幅正轉 +500 counts (耗時 2.0 秒，每 1ms 平滑插補)")
    print("=" * 65)
    execute_move(base + 500, dur=2.0)
    time.sleep(1.0)

    print("\n" + "=" * 65)
    print("測試 2: 返回基準位置 (500 -> 0)")
    print("=" * 65)
    execute_move(base, dur=2.0)
    time.sleep(1.0)

    print("\n" + "=" * 65)
    print("測試 3: 微幅反轉 -500 counts (耗時 2.0 秒)")
    print("=" * 65)
    execute_move(base - 500, dur=2.0)
    time.sleep(1.0)

    print("\n" + "=" * 65)
    print("測試 4: 返回基準位置 (-500 -> 0)")
    print("=" * 65)
    execute_move(base, dur=2.0)
    print("\n[+] 測試全部完成。")

except KeyboardInterrupt:
    print("\n[*] 使用者中斷測試。")

finally:
    current_cw = 0x0000
    time.sleep(0.2)
    running = False
    th.join()
    slave.output = bytes(len(slave.output))
    master.send_processdata()
    master.close()
    print("[*] 伺服已安全斷電釋放。")
