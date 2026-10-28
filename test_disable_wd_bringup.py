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

# 1. 禁用 SyncManager Watchdog (避免 Linux 抖動導致 Outputs 被 ESC 凍結)
try:
    # 0x0420: Watchdog Time PDI, 0x0442: Watchdog Time Process Data
    slave.reg_write(0x0442, struct.pack("<H", 0))
    print("[+] 成功禁用 SM Watchdog (設為 0)")
except Exception as e:
    print(f"[!] 寫入暫存器提示: {e}")

# 2. 配置 DC 同步
cycle_time_ns = 1_000_000
try:
    slave.dc_sync(True, cycle_time_ns, cycle_time_ns)
    master.config_dc()
    print("[+] 已配置 DC 模式 (SYNC0/SYNC1 = 1ms)")
except Exception as e:
    print(f"[!] DC 配置提示: {e}")

# 3. 映射 IO
master.config_map()

# 嚴格 14 Bytes 方案 A 打包 (<Hiib3x)
def pack_rxpdo(cw, pos=0, vel=0, mode=8):
    return struct.pack("<Hiib3x", cw, pos, vel, mode)

def unpack_txpdo(data):
    if len(data) >= 14:
        return struct.unpack("<Hiib3x", data[:14])
    return 0, 0, 0, 0

current_cw = 0x0000
last_sw = 0
last_pos = 0
last_wkc = 0
running = True

# 4. 背景 1ms 即時傳輸執行緒
def pdo_cycle():
    global last_sw, last_pos, last_wkc, running
    while running:
        t0 = time.perf_counter()
        
        slave.output = pack_rxpdo(current_cw, last_pos, 0, 8)
        master.send_processdata()
        wkc = master.receive_processdata(2000)
        last_wkc = wkc

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

# 5. 推進 SAFEOP 並預熱
print("[*] 推進至 SAFEOP 狀態...")
slave.state = pysoem.SAFEOP_STATE
master.write_state()
master.state_check(pysoem.SAFEOP_STATE, 50000)
print("[+] 已進入 SAFEOP，預熱 1.0 秒...")
time.sleep(1.0)

# 6. 推進至 OP 狀態
print("[*] 推進至 OP 狀態...")
slave.state = pysoem.OP_STATE
master.write_state()
master.state_check(pysoem.OP_STATE, 50000)
print(f"[+] 從站成功進入狀態: {hex(slave.state)} (0x8 代表 OP)")

# 7. 標準 CiA 402 狀態推進序列
sequence = [
    (0x0080, 0.5, "1. Fault Reset (0x0080)"),
    (0x0000, 0.3, "2. 復位清零 (0x0000)"),
    (0x0006, 0.8, "3. Shutdown (0x0006 -> 期待 0x1221 / 0x1240)"),
    (0x0007, 0.8, "4. Switch On (0x0007 -> 期待 0x1223)"),
    (0x000F, 2.0, "5. Enable Operation (0x000F -> 期待 0x1227 激磁鎖軸)"),
]

for cw, dur, desc in sequence:
    print(f"\n[*] 執行階段: {desc}")
    current_cw = cw
    t_end = time.time() + dur
    while time.time() < t_end:
        print(f"    SW: 0x{last_sw:04X} | WKC: {last_wkc} | 實際位置: {last_pos:6d}", end="\r")
        time.sleep(0.05)
    print()

running = False
th.join()
slave.output = bytes(len(slave.output))
master.send_processdata()
master.close()
print(f"\n[*] 測試流程結束，最終 SW: 0x{last_sw:04X}")
