import pysoem
import struct
import time
import threading

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到從站！")
    exit()

slave = master.slaves[0]
print(f"[+] 找到從站: {slave.name}")

master.config_map()

# 依照 0x1600 / 0x1A00 結構 (<Hiib3x)
def pack_rxpdo(cw, pos=0, vel=0, mode=8):
    return struct.pack("<Hiib3x", cw, pos, vel, mode)

def unpack_txpdo(data):
    if len(data) >= 14:
        return struct.unpack("<Hiib3x", data[:14])
    return 0, 0, 0, 0

current_cw = 0x0000
target_pos = 0
last_sw = 0
last_pos = 0
last_vel = 0
last_mode = 0
last_wkc = 0
running = True

def pdo_cycle():
    global last_sw, last_pos, last_vel, last_mode, last_wkc, running
    while running:
        t0 = time.perf_counter()
        
        slave.output = pack_rxpdo(current_cw, target_pos, 0, 8)
        master.send_processdata()
        wkc = master.receive_processdata(2000)
        last_wkc = wkc

        if wkc > 0 and slave.input:
            sw, pos, vel, mode = unpack_txpdo(slave.input)
            last_sw = sw
            last_pos = pos
            last_vel = vel
            last_mode = mode

        elapsed = time.perf_counter() - t0
        delay = 0.001 - elapsed
        if delay > 0:
            time.sleep(delay)

th = threading.Thread(target=pdo_cycle, daemon=True)
th.start()

def read_u16(addr):
    try:
        raw = slave._fprd(addr, 2)
        return struct.unpack("<H", raw)[0]
    except Exception:
        return None

def write_u16(addr, val):
    slave._fpwr(addr, struct.pack("<H", val))

# 1. 推進至 SAFEOP
slave.state = pysoem.SAFEOP_STATE
master.write_state()
master.state_check(pysoem.SAFEOP_STATE, 50000)
time.sleep(0.5)

# 2. 清除可能殘留的 Error Flag，確保落於乾淨的 SAFEOP
cur_stat = read_u16(0x0130)
if cur_stat and (cur_stat & 0x0010):
    write_u16(0x0120, 0x0014)
    time.sleep(0.2)
write_u16(0x0120, 0x0004)
time.sleep(0.3)

# 3. 強制推入真實 OP (0x0120 = 0x0008)
print("[*] 正在將從站推入真實 OP 狀態...")
write_u16(0x0120, 0x0008)
time.sleep(0.3)

real_stat = read_u16(0x0130)
print(f"[+] 從站真實狀態: 0x{real_stat:04X} ({'OP 狀態確立' if real_stat == 0x0008 else '未進入 OP'})")

# 4. 同步當前位置
target_pos = last_pos
print(f"[+] 起始反饋對齊: 實際位置 = {target_pos}, SW = 0x{last_sw:04X}")

# 5. CiA 402 狀態機跳轉序列
steps = [
    (0x0000, 0.4, "1. 清除控制字 (0x0000)"),
    (0x0080, 0.4, "2. Fault Reset 復位上升沿 (0x0080)"),
    (0x0000, 0.3, "3. 清除復位 (0x0000)"),
    (0x0006, 0.8, "4. Shutdown (0x0006 -> 期待 Ready to Switch On)"),
    (0x0007, 0.8, "5. Switch On (0x0007 -> 期待 Switched On)"),
    (0x000F, 3.0, "6. Enable Operation (0x000F -> 期待 Operation Enabled 激磁鎖軸)"),
]

for cw, dur, desc in steps:
    print(f"\n[*] 執行步驟: {desc}")
    current_cw = cw
    target_pos = last_pos
    t_end = time.time() + dur
    while time.time() < t_end:
        print(f"    下發CW: 0x{current_cw:04X} | 反饋SW: 0x{last_sw:04X} | 實際位置: {last_pos:6d} | 速度: {last_vel:5d} | 模式: {last_mode} | WKC: {last_wkc}", end="\r")
        time.sleep(0.05)
    print()

running = False
th.join()
slave.output = bytes(len(slave.output))
master.send_processdata()
master.close()
print(f"\n[*] 測試流程結束，最終 SW: 0x{last_sw:04X}")
