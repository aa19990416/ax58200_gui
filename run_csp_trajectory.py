import pysoem
import struct
import time
import math
import threading

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到 EtherCAT 從站！")
    exit()

slave = master.slaves[0]
print(f"[+] 找到從站: {slave.name}")

master.config_map()

def pack_rxpdo(cw, pos=0, vel=0, mode=8):
    return struct.pack("<Hiib3x", cw, pos, vel, mode)

def unpack_txpdo(data):
    if len(data) >= 14:
        return struct.unpack("<Hiib3x", data[:14])
    return 0, 0, 0, 0

current_cw = 0x0000
target_pos = 0
current_mode = 8

last_sw = 0
actual_pos = 0
actual_vel = 0
running = True

def pdo_loop():
    global last_sw, actual_pos, actual_vel, running
    while running:
        t0 = time.perf_counter()
        slave.output = pack_rxpdo(current_cw, target_pos, 0, current_mode)
        master.send_processdata()
        wkc = master.receive_processdata(2000)

        if wkc > 0 and slave.input:
            sw, pos, vel, mode = unpack_txpdo(slave.input)
            last_sw = sw
            actual_pos = pos
            actual_vel = vel

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

# Servo-On 流程
target_pos = actual_pos
time.sleep(0.1)

print("[*] 執行 Servo-On...")
for cw, desc in [(0x0006, "Shutdown"), (0x0007, "Switch On"), (0x000F, "Enable Operation")]:
    current_cw = cw
    t_end = time.time() + 0.5
    while time.time() < t_end:
        if cw == 0x000F and (last_sw & 0x006F) == 0x0027:
            break
        time.sleep(0.02)

if (last_sw & 0x006F) != 0x0027:
    print(f"[-] Servo-On 失敗，SW = 0x{last_sw:04X}")
    running = False
    th.join()
    exit()

print(f"[+] 伺服使能完成！起始位置: {actual_pos}")

# 軌跡設定：振幅 5000 counts，週期 4 秒，執行 2 個完整週期
amplitude = 5000
cycle_time = 4.0
total_time = 8.0
start_origin = actual_pos

print(f"[*] 開始執行正弦波運動軌跡 (振幅: ±{amplitude} counts, 週期: {cycle_time}s)...")
t_start = time.time()

try:
    while time.time() - t_start < total_time:
        t_cur = time.time() - t_start
        # 1 - cos(wt) 平滑啟動正弦軌跡
        omega = 2.0 * math.pi / cycle_time
        target_pos = int(start_origin + amplitude * math.sin(omega * t_cur))
        
        diff = target_pos - actual_pos
        print(f"  目標: {target_pos:6d} | 實際: {actual_pos:6d} | 誤差: {diff:4d} counts | 速度: {actual_vel:5d}", end="\r")
        time.sleep(0.005)
    print("\n[+] 軌跡運動完成。")

except KeyboardInterrupt:
    print("\n[*] 使用者中斷運動。")

finally:
    print("[*] 正在退使能並關閉連線...")
    current_cw = 0x0000
    time.sleep(0.2)
    running = False
    th.join()
    slave.output = bytes(len(slave.output))
    master.send_processdata()
    master.close()
    print("[*] 釋放完成。")
