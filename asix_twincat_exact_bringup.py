import pysoem
import time
import struct
import threading

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到從站！請檢查網路連線。")
    exit()

slave = master.slaves[0]
print(f"[+] 找到從站: {slave.name}")

# 1. 依照手冊 Figure 5-46: 啟用 DC 同步 (SYNC0 = 1ms, SYNC1 = 1ms)
cycle_time_ns = 1_000_000
try:
    slave.dc_sync(True, cycle_time_ns, cycle_time_ns)
    master.config_dc()
    print("[+] 已配置 DC 模式 (SYNC0/SYNC1 = 1ms)")
except Exception as e:
    print(f"[!] DC 配置提示: {e}")

# 2. 自動配置 PDO 映射
master.config_map()

# 依照 Table 5-2: 嚴格對應 11 Bytes (<Hiib)
# H (Controlword: 2B) + i (TargetPos: 4B) + i (TargetVel: 4B) + b (Mode: 1B)
def pack_rxpdo(cw, pos=0, vel=0, mode=8):
    return struct.pack("<Hiib", cw, pos, vel, mode)

def unpack_txpdo(data):
    if len(data) >= 11:
        return struct.unpack("<Hiib", data[:11])
    return 0, 0, 0, 0

# 全局共享變數
current_cw = 0x0000
last_sw = 0
last_pos = 0
last_vel = 0
last_mode = 0
running = True

# 3. 獨立 1ms 背景 PDO 週期通訊執行緒
def pdo_cycle():
    global last_sw, last_pos, last_vel, last_mode, running
    while running:
        t0 = time.perf_counter()
        
        slave.output = pack_rxpdo(current_cw, last_pos, 0, 8)
        master.send_processdata()
        wkc = master.receive_processdata(2000)

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
print("[+] 已進入 SAFEOP，維持 1ms Process Data 封包流以鎖定 DC 時鐘 (1.5 秒)...")
time.sleep(1.5)

# 5. 推進至 OP 狀態
print("[*] 推進至 OP 狀態...")
slave.state = pysoem.OP_STATE
master.write_state()
master.state_check(pysoem.OP_STATE, 50000)
print(f"[+] 從站成功進入狀態: {hex(slave.state)} (0x8 代表 OP)")

# 6. 依照 Figure 5-56/5-57 執行狀態推進序列
sequence = [
    (0x0080, 0.5, "1. 下發 Fault Reset (0x0080)"),
    (0x0000, 0.3, "2. 復位清零 (0x0000)"),
    (0x0006, 0.6, "3. 下發 Shutdown (0x0006 -> 期待 0x1221 / 0x1240)"),
    (0x0007, 0.6, "4. 下發 Switch On (0x0007 -> 期待 0x1223)"),
    (0x000F, 2.0, "5. 下發 Enable Operation (0x000F -> 期待 0x1227 激磁鎖軸)"),
]

for cw, duration, desc in sequence:
    print(f"\n[*] 執行階段: {desc}")
    current_cw = cw
    t_end = time.time() + duration
    while time.time() < t_end:
        print(f"    SW: 0x{last_sw:04X} | 實際位置: {last_pos:6d} | 速度: {last_vel:5d} | 模式: {last_mode}", end="\r")
        time.sleep(0.1)
    print()

running = False
th.join()
slave.output = bytes(len(slave.output))
master.send_processdata()
master.close()
print(f"\n[*] 測試完成，最終 SW: 0x{last_sw:04X}")
