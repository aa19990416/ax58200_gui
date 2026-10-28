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

# 讀取母線電壓相關物件 (如果有)
def read_sdo_u16(idx, sub):
    try:
        return struct.unpack("<H", slave.sdo_read(idx, sub))[0]
    except Exception:
        return None

master.config_map()

def pack_rxpdo(cw, pos=0, vel=0, mode=9):
    return struct.pack("<Hiib3x", cw, pos, vel, mode)

def unpack_txpdo(data):
    if len(data) >= 14:
        return struct.unpack("<Hiib3x", data[:14])
    return 0, 0, 0, 0

current_cw = 0x0000
target_pos = 0
target_vel = 0
current_mode = 9

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

slave.state = pysoem.SAFEOP_STATE
master.write_state()
master.state_check(pysoem.SAFEOP_STATE, 50000)
time.sleep(0.1)

slave._fpwr(0x0120, struct.pack("<H", 0x0004))
time.sleep(0.1)
slave._fpwr(0x0120, struct.pack("<H", 0x0008))
time.sleep(0.2)

# 清除 Fault
if (last_sw & 0x0008) != 0:
    current_cw = 0x0000
    time.sleep(0.1)
    current_cw = 0x0080
    time.sleep(0.3)
    current_cw = 0x0000
    time.sleep(0.1)

# 使能
current_cw = 0x0006
time.sleep(0.2)
current_cw = 0x0007
time.sleep(0.1)
current_cw = 0x000F
time.sleep(0.5)

print(f"[+] 使能就緒！SW = 0x{last_sw:04X} | 模式: {actual_mode}")
print("[*] 請用手稍微摸一下馬達軸，感受現在是否有電磁阻抗（鎖軸力）？")
time.sleep(1.5)

print("\n[*] 依原廠規格下發 Target Velocity = 1000 rpm (持續 3 秒)...")
target_vel = 1000

t_end = time.time() + 3.0
while time.time() < t_end:
    sys.stdout.write(f"\r  [CSV運轉] 指令速度: {target_vel:4d} | 實際轉速: {actual_vel:4d} | 實際位置: {actual_pos:6d}")
    sys.stdout.flush()
    time.sleep(0.05)
print()

target_vel = 0
time.sleep(0.5)
current_cw = 0x0000
running = False
th.join()
master.close()
print("[+] 測試結束。")
