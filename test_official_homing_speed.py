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

def sdo_r_u32(idx, sub):
    try:
        return struct.unpack("<I", slave.sdo_read(idx, sub))[0]
    except Exception as e:
        return f"Fail: {e}"

def sdo_w_u32(idx, sub, val):
    try:
        slave.sdo_write(idx, sub, struct.pack("<I", val))
        return True
    except Exception as e:
        return False

# 讀取當前預設速度
sp1 = sdo_r_u32(0x6099, 1)
sp2 = sdo_r_u32(0x6099, 2)
print(f"[*] 原始 0x6099:01 (Switch Speed) = {sp1}")
print(f"[*] 原始 0x6099:02 (Zero Speed)   = {sp2}")

# 依官方展示配置：寫入 600 (對應 600 rpm 或足夠帶動轉子的數值)
sdo_w_u32(0x6099, 1, 600)
sdo_w_u32(0x6099, 2, 300)
print(f"[+] 重新配置 0x6099 = 600 / 300")

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
    current_cw = 0x0080
    time.sleep(0.2)
    current_cw = 0x0000
    time.sleep(0.1)

# 使能
for cw in [0x0006, 0x0007, 0x000F]:
    current_cw = cw
    target_pos = actual_pos
    time.sleep(0.2)

print(f"[+] 使能完成！SW = 0x{last_sw:04X} | 起始位置: {actual_pos}")
print("[*] 觸發 Homing 運轉 (CW = 0x001F)...")
current_cw = 0x001F

t_start = time.time()
while time.time() - t_start < 6.0:
    attained = bool(last_sw & 0x1000)
    reached = bool(last_sw & 0x0400)
    error = bool(last_sw & 0x2000)
    sys.stdout.write(f"\r  SW: 0x{last_sw:04X} | 位置: {actual_pos:6d} | 轉速: {actual_vel:5d} | Attained: {attained} | Reached: {reached}")
    sys.stdout.flush()
    if attained and reached:
        print(f"\n[!!! 成功 !!!] Homing 到位完成！")
        break
    if error:
        print(f"\n[-] 錯誤報警！SW: 0x{last_sw:04X}")
        break
    time.sleep(0.02)

current_cw = 0x000F
time.sleep(0.5)
current_cw = 0x0000
running = False
th.join()
master.close()
print("\n[*] 測試結束。")
