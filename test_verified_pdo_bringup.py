import pysoem
import time
import struct
import threading

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到從站！請檢查網路介面與接線。")
    exit()

slave = master.slaves[0]
print(f"[+] 找到從站: {slave.name}")

# 1. 依照原廠手冊配置 DC 同步 (1ms = 1,000,000 ns)
cycle_time_ns = 1_000_000
try:
    slave.dc_sync(True, cycle_time_ns, cycle_time_ns)
    master.config_dc()
    print("[+] 已配置 DC 模式 (SYNC0/SYNC1 = 1ms)")
except Exception as e:
    print(f"[!] DC 配置提示: {e}")

# 2. 自動配置 PDO (長度確認為 Rx: 14B, Tx: 14B)
master.config_map()

# 依照 0x1600 (RxPDO) 與 0x1A00 (TxPDO) 映射結構嚴格封裝:
# <Hiib3x:
#   H : 0x6040 / 0x6041 (16 bits / 2 Bytes)
#   i : 0x607A / 0x6064 (32 bits / 4 Bytes)
#   i : 0x60FF / 0x606C (32 bits / 4 Bytes)
#   b : 0x6060 / 0x6061 ( 8 bits / 1 Byte)
#  3x : 0x0000 Padding  (24 bits / 3 Bytes)
def pack_rxpdo(cw, pos=0, vel=0, mode=8):
    return struct.pack("<Hiib3x", cw, pos, vel, mode)

def unpack_txpdo(data):
    if len(data) >= 14:
        return struct.unpack("<Hiib3x", data[:14])
    return 0, 0, 0, 0

# 全局共享變數
current_cw = 0x0000
target_pos = 0
last_sw = 0
last_pos = 0
last_vel = 0
last_mode = 0
last_wkc = 0
running = True

# 3. 獨立 1ms 背景 PDO 週期通訊執行緒
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

# 4. 推進至 SAFEOP 狀態並進行 DC 鎖定預熱
print("[*] 推進至 SAFEOP 狀態...")
slave.state = pysoem.SAFEOP_STATE
master.write_state()
master.state_check(pysoem.SAFEOP_STATE, 50000)
print("[+] 已進入 SAFEOP，維持 Process Data 封包流預熱 1.5 秒...")
time.sleep(1.5)

# 5. 推進至 OP 狀態
print("[*] 推進至 OP 狀態...")
slave.state = pysoem.OP_STATE
master.write_state()
master.state_check(pysoem.OP_STATE, 50000)
print(f"[+] 從站成功進入狀態: {hex(slave.state)} (0x8 代表 OP)")

# 6. 位置與模式同步對齊
time.sleep(0.3)
target_pos = last_pos
print(f"[+] 反饋對齊: 起始位置 = {last_pos}, 當前 SW = 0x{last_sw:04X}, WKC = {last_wkc}")

# 7. 標準 CiA 402 狀態機跳轉序列 (完整上升沿時序)
steps = [
    (0x0000, 0.3, "1. 清除控制字 (0x0000)"),
    (0x0080, 0.4, "2. Fault Reset 復位上升沿 (0x0080)"),
    (0x0000, 0.3, "3. 清除復位 (0x0000)"),
    (0x0006, 0.8, "4. Shutdown (0x0006 -> 期待 0x1221 或 0x1240)"),
    (0x0007, 0.8, "5. Switch On (0x0007 -> 期待 0x1223)"),
    (0x000F, 2.0, "6. Enable Operation (0x000F -> 期待 0x1227 激磁鎖軸)"),
]

for cw, dur, desc in steps:
    print(f"\n[*] 執行步驟: {desc}")
    current_cw = cw
    target_pos = last_pos
    t_end = time.time() + dur
    while time.time() < t_end:
        print(f"    SW: 0x{last_sw:04X} | 實際位置: {last_pos:6d} | 速度: {last_vel:5d} | 模式: {last_mode} | WKC: {last_wkc}", end="\r")
        time.sleep(0.05)
    print()

running = False
th.join()
slave.output = bytes(len(slave.output))
master.send_processdata()
master.close()
print(f"\n[*] 測試流程結束，最終 SW: 0x{last_sw:04X}")
