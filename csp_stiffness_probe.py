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

for cw in [0x0006, 0x0007, 0x000F]:
    current_cw = cw
    target_pos = actual_pos
    time.sleep(0.2)

current_cw = 0x001F
t_end = time.time() + 6.0
while time.time() < t_end:
    if last_sw & 0x1000:
        break
    time.sleep(0.05)

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

# 鎖定在當前物理位置為固定閉環目標
target_pos = actual_pos
target_vel = 0
current_cw = 0x0007
time.sleep(0.1)
current_cw = 0x000F
time.sleep(0.2)

print("\n" + "=" * 70)
print(f"[+] 閉環鎖定啟動！SW = 0x{last_sw:04X} | 固定目標 Target = {target_pos}")
print("    - 請試著用手扳動馬達軸（順時針與逆時針）")
print("    - 觀察軸心是否有抵抗力矩，以及放手後是否會自動彈回目標位置")
print("    - 隨時按下 [Ctrl + C] 結束測試")
print("=" * 70)

try:
    while True:
        diff = target_pos - actual_pos
        sys.stdout.write(
            f"\r  [位置閉環監測] 鎖定目標: {target_pos:6d} | 實際位置: {actual_pos:6d} | 誤差: {diff:5d} | 反饋速度: {actual_vel:4d}"
        )
        sys.stdout.flush()
        time.sleep(0.05)

except KeyboardInterrupt:
    print("\n\n[*] 收到中斷信號...")

finally:
    current_cw = 0x0000
    time.sleep(0.2)
    running = False
    th.join()
    slave.output = bytes(len(slave.output))
    master.send_processdata()
    master.close()
    print("[*] 伺服已安全斷電釋放。")
