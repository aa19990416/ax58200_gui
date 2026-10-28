import pysoem
import time
import struct
import threading

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到從站！")
    exit()

slave = master.slaves[0]
print(f"[+] 找到從站: {slave.name}")

# 【核心】：完全不啟用 DC 同步！不呼叫 dc_sync 與 config_dc，避開 PLL 200ms 鎖定檢驗！
master.config_map()

# 嚴格 11 Bytes 打包
def pack_rx(cw, pos, vel, mode):
    return struct.pack("<Hiib", cw, pos, vel, mode)

def unpack_tx(data):
    if len(data) >= 11:
        return struct.unpack("<Hiib", data[:11])
    return 0, 0, 0, 0

current_cw = 0x0000
last_sw = 0
last_pos = 0
running = True

def pdo_loop():
    global last_sw, last_pos, running
    while running:
        t0 = time.perf_counter()
        slave.output = pack_rx(current_cw, last_pos, 0, 8)
        master.send_processdata()
        wkc = master.receive_processdata(2000)
        if wkc > 0 and slave.input:
            sw, pos, vel, mode = unpack_tx(slave.input)
            last_sw = sw
            last_pos = pos
        elapsed = time.perf_counter() - t0
        sleep_t = 0.001 - elapsed
        if sleep_t > 0:
            time.sleep(sleep_t)

th = threading.Thread(target=pdo_loop, daemon=True)
th.start()

# 逐步推進
slave.state = pysoem.SAFEOP_STATE
master.write_state()
master.state_check(pysoem.SAFEOP_STATE, 50000)
print("[*] SAFEOP 預熱中...")
time.sleep(0.5)

slave.state = pysoem.OP_STATE
master.write_state()
master.state_check(pysoem.OP_STATE, 50000)
print(f"[+] OP 狀態: {hex(slave.state)}")

sequence = [
    (0x0080, 0.4, "1. Fault Reset (0x0080)"),
    (0x0000, 0.2, "2. 復位清零 (0x0000)"),
    (0x0006, 0.5, "3. Shutdown (0x0006 -> 期待 0x1221/0x1240)"),
    (0x0007, 0.5, "4. Switch On (0x0007 -> 期待 0x1223)"),
    (0x000F, 1.5, "5. Enable Operation (0x000F -> 期待 0x1227)"),
]

for cw, dur, desc in sequence:
    print(f"\n[*] {desc}")
    current_cw = cw
    t_end = time.time() + dur
    while time.time() < t_end:
        print(f"    SW: 0x{last_sw:04X} | Pos: {last_pos:6d}", end="\r")
        time.sleep(0.1)
    print()

running = False
th.join()
slave.output = bytes(len(slave.output))
master.send_processdata()
master.close()
print(f"\n[*] 結束，最終 SW: 0x{last_sw:04X}")
