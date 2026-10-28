import pysoem
import time
import struct
import threading

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到從站！請檢查網路線連接與供電。")
    exit()

slave = master.slaves[0]
print(f"[+] 找到從站: {slave.name}")

# 1. 依照手冊 Figure 5-46: 同時啟用 SYNC0 與 SYNC1 (1ms = 1,000,000 ns)
cycle_ns = 1_000_000
try:
    slave.dc_sync(True, cycle_ns, cycle_ns)
    master.config_dc()
    print("[+] 成功配置 DC (SYNC0 + SYNC1 均為 1ms)")
except Exception as e:
    print(f"[!] DC 配置提示: {e}")

# 2. 自動映射 PDO
master.config_map()

# 依照 Table 5-2: 嚴格對應 11 Bytes (<Hiib)
# H (UINT: 2) + i (DINT: 4) + i (DINT: 4) + b (SINT: 1) = 11 Bytes
def pack_rx(cw, pos, vel, mode):
    return struct.pack("<Hiib", cw, pos, vel, mode)

def unpack_tx(data):
    if len(data) >= 11:
        return struct.unpack("<Hiib", data[:11])
    return 0, 0, 0, 0

# 全局共享變數
current_cw = 0x0000
last_sw = 0
last_pos = 0
running = True

# 3. 獨立 1ms 背景 PDO 週期通訊執行緒
def pdo_thread():
    global last_sw, last_pos, running
    while running:
        t0 = time.perf_counter()
        
        # 發送當前控制字與目標位置 (CSP 模式 Mode=8)
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

th = threading.Thread(target=pdo_thread, daemon=True)
th.start()

# 4. 推進至 SAFEOP 並等待時鐘與通訊鎖定 (1 秒)
slave.state = pysoem.SAFEOP_STATE
master.write_state()
master.state_check(pysoem.SAFEOP_STATE, 50000)
print("[*] 進入 SAFEOP，正在進行時鐘與 Process Data 預熱 (1000ms)...")
time.sleep(1.0)

# 5. 推進至 OP 狀態
slave.state = pysoem.OP_STATE
master.write_state()
master.state_check(pysoem.OP_STATE, 50000)
print(f"[+] 從站成功進入 OP 狀態: {hex(slave.state)}")

# 6. CiA 402 標準狀態機推進序列
sequence = [
    (0x0080, 0.4, "1. Fault Reset 復位 (下發 0x0080)"),
    (0x0000, 0.2, "2. 復位清零 (下發 0x0000)"),
    (0x0006, 0.5, "3. Shutdown (下發 0x0006 -> 期待 0x1221 / 0x1240)"),
    (0x0007, 0.5, "4. Switch On (下發 0x0007 -> 期待 0x1223)"),
    (0x000F, 1.5, "5. Enable Operation (下發 0x000F -> 期待 0x1227 激磁鎖軸)"),
]

for cw, dur, desc in sequence:
    print(f"\n[*] 執行階段: {desc}")
    current_cw = cw
    t_end = time.time() + dur
    while time.time() < t_end:
        print(f"    SW: 0x{last_sw:04X} | 實際位置: {last_pos:6d}", end="\r")
        time.sleep(0.1)
    print()

# 結束處理
running = False
th.join()
slave.output = bytes(len(slave.output))
master.send_processdata()
master.close()
print(f"\n[*] 測試流程結束，最終 SW: 0x{last_sw:04X}")
