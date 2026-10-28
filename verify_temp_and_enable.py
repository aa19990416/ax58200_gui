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

# 先以 PREOP 快速確認 0x31B1 是否已排除
slave.state = pysoem.PREOP_STATE
master.write_state()
master.state_check(pysoem.PREOP_STATE, 50000)
time.sleep(0.2)

try:
    mcfs = int.from_bytes(slave.sdo_read(0x31B1, 0), 'little')
    print(f"[*] 當前 MC Fault Status (0x31B1): 0x{mcfs:04X}")
    if mcfs & 0x0004:
        print("[-] 警告: 0x31B1 仍為 0x0004 (過溫保護中)，請先接上 NTC/電阻或更新韌體！")
    else:
        print("[+] 恭喜！過溫保護已解除 (0x31B1 無 0x0004 旗標)。")
except Exception as e:
    print(f"[-] 讀取 0x31B1 失敗: {e}")

# 配置 PDO
master.config_map()

def pack_rxpdo(cw, pos=0, vel=0, mode=8):
    return struct.pack("<Hiib3x", cw, pos, vel, mode)

def unpack_txpdo(data):
    if len(data) >= 14:
        return struct.unpack("<Hiib3x", data[:14])
    return 0, 0, 0, 0

current_cw = 0x0000
last_sw = 0
last_pos = 0
running = True

def pdo_cycle():
    global last_sw, last_pos, running
    while running:
        t0 = time.perf_counter()
        slave.output = pack_rxpdo(current_cw, last_pos, 0, 8)
        master.send_processdata()
        wkc = master.receive_processdata(2000)
        if wkc > 0 and slave.input:
            sw, pos, vel, mode = unpack_txpdo(slave.input)
            last_sw = sw
            last_pos = pos
        elapsed = time.perf_counter() - t0
        delay = 0.001 - elapsed
        if delay > 0:
            time.sleep(delay)

th = threading.Thread(target=pdo_cycle, daemon=True)
th.start()

# 推進至 OP
slave._fpwr(0x0120, struct.pack("<H", 0x0004))
time.sleep(0.1)
slave._fpwr(0x0120, struct.pack("<H", 0x0008))
time.sleep(0.3)

print("\n[*] 依序下發 CiA 402 使能序列...")
steps = [
    (0x0000, 0.2, "Clear CW (0x0000)"),
    (0x0080, 0.4, "Fault Reset (0x0080)"),
    (0x0000, 0.2, "Clear Reset (0x0000)"),
    (0x0006, 0.5, "Shutdown (0x0006 -> 期待 SW=0x1221)"),
    (0x0007, 0.5, "Switch On (0x0007 -> 期待 SW=0x1223)"),
    (0x000F, 2.0, "Enable Operation (0x000F -> 期待 SW=0x1227 激磁鎖軸)"),
]

for cw, dur, desc in steps:
    print(f"  -> {desc}")
    current_cw = cw
    t_end = time.time() + dur
    while time.time() < t_end:
        print(f"     下發 CW: 0x{current_cw:04X} | 反饋 SW: 0x{last_sw:04X} | 實際位置: {last_pos}", end="\r")
        time.sleep(0.05)
    print()

running = False
th.join()
slave.output = bytes(len(slave.output))
master.send_processdata()
master.close()
print(f"\n[*] 執行結束，最終 SW: 0x{last_sw:04X}")
