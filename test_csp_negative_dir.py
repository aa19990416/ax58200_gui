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

# 步驟 1: Homing 模式使能
for cw in [0x0006, 0x0007, 0x000F]:
    current_cw = cw
    target_pos = actual_pos
    time.sleep(0.2)

# 步驟 2: 觸發 Homing 標定
current_cw = 0x001F
t_end = time.time() + 6.0
while time.time() < t_end:
    if last_sw & 0x1000:
        break
    time.sleep(0.05)
print(f"[+] Homing 標定完成！SW = 0x{last_sw:04X} | 原點基準: {actual_pos}")

# 步驟 3: 降使能換模式至 CSP (Mode 8)
current_cw = 0x0006
time.sleep(0.15)
current_mode = 8
target_pos = actual_pos
time.sleep(0.15)

t_m = time.time() + 1.5
while time.time() < t_m:
    if actual_mode == 8:
        break
    time.sleep(0.02)

target_pos = actual_pos
target_vel = 0
current_cw = 0x0007
time.sleep(0.1)
current_cw = 0x000F
time.sleep(0.2)

print(f"[+] 激磁完成！SW = 0x{last_sw:04X} | 模式: {actual_mode} | 當前位置: {actual_pos}")

def move_linear(delta_counts, speed_counts_per_sec):
    global target_pos, target_vel
    start_p = target_pos
    direction = 1 if delta_counts >= 0 else -1
    total_counts = abs(delta_counts)
    dur = total_counts / float(speed_counts_per_sec)
    
    t_start = time.perf_counter()
    while True:
        elapsed = time.perf_counter() - t_start
        if elapsed >= dur:
            target_pos = start_p + delta_counts
            target_vel = 0
            break
        
        curr_offset = int(elapsed * speed_counts_per_sec * direction)
        target_pos = start_p + curr_offset
        target_vel = int(speed_counts_per_sec * direction)
        
        diff = target_pos - actual_pos
        sys.stdout.write(f"\r  [運動中] 目標: {target_pos:6d} | 實際: {actual_pos:6d} | 誤差: {diff:5d} | 反饋速度: {actual_vel:4d}")
        sys.stdout.flush()
        time.sleep(0.002)
    print()

try:
    print("\n" + "=" * 65)
    print("極性驗證：改向【負方向】下發 -600 counts (速度 300 counts/s)")
    print("=" * 65)
    move_linear(-600, 300)
    time.sleep(1.0)

    print("\n" + "=" * 65)
    print("返回原點 (+600 counts)")
    print("=" * 65)
    move_linear(600, 300)
    time.sleep(1.0)

except KeyboardInterrupt:
    print("\n[*] 使用者中斷。")

finally:
    current_cw = 0x0000
    time.sleep(0.2)
    running = False
    th.join()
    slave.output = bytes(len(slave.output))
    master.send_processdata()
    master.close()
    print("[*] 伺服已安全斷電釋放。")
