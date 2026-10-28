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

# 1. 配置 DC 同步
cycle_time_ns = 1_000_000
try:
    slave.dc_sync(True, cycle_time_ns, cycle_time_ns)
    master.config_dc()
    print("[+] 已配置 DC 模式 (SYNC0/SYNC1 = 1ms)")
except Exception as e:
    print(f"[!] DC 配置提示: {e}")

# 2. 自動映射 (14 Bytes)
master.config_map()

# 打包與解包函式 (<Hiib3x)
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
running = True

# 3. 獨立 1ms 背景 PDO 週期通訊執行緒
def pdo_cycle():
    global last_sw, last_pos, running
    while running:
        t0 = time.perf_counter()
        slave.output = pack_rxpdo(current_cw, target_pos, 0, 8)
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

# 4. 推進至 SAFEOP 狀態
print("[*] 推進至 SAFEOP 狀態...")
slave.state = pysoem.SAFEOP_STATE
master.write_state()
master.state_check(pysoem.SAFEOP_STATE, 50000)
time.sleep(1.5)

# 5. 推進至 OP 狀態
print("[*] 推進至 OP 狀態...")
slave.state = pysoem.OP_STATE
master.write_state()
master.state_check(pysoem.OP_STATE, 50000)
print(f"[+] 從站成功進入狀態: {hex(slave.state)} (0x8 代表 OP)")

# 6. 對齊位置
time.sleep(0.2)
target_pos = last_pos

# 7. 關鍵動作：模擬手冊「Step 10: Power On」動作
print("\n[*] 執行 Step 10: Power On 操作 (寫入 0x60FE 輸出與使能)...")
try:
    # 允許所有輸出遮罩
    slave.sdo_write(0x60FE, 2, struct.pack("<I", 0xFFFFFFFF))
    # 將 Physical Outputs 第一位拉高 (啟用功率級 / 繼電器 / Gate Driver)
    slave.sdo_write(0x60FE, 1, struct.pack("<I", 0x00000001))
    print("[+] 成功寫入 0x60FE Physical Outputs 使能信號！")
except Exception as e:
    print(f"[!] 0x60FE 寫入提示: {e}")

time.sleep(0.5)

# 8. CiA 402 狀態機推進序列
sequence = [
    (0x0080, 0.5, "Fault Reset (0x0080)"),
    (0x0000, 0.3, "復位清零 (0x0000)"),
    (0x0006, 0.8, "Shutdown (0x0006 -> 期待 0x1221 / 0x1240)"),
    (0x0007, 0.8, "Switch On (0x0007 -> 期待 0x1223)"),
    (0x000F, 2.0, "Enable Operation (0x000F -> 期待 0x1227 激磁鎖軸)"),
]

for cw, dur, desc in sequence:
    print(f"\n[*] 執行步驟: {desc}")
    current_cw = cw
    t_end = time.time() + dur
    while time.time() < t_end:
        print(f"    SW: 0x{last_sw:04X} | 實際位置: {last_pos:6d} | 目標位置: {target_pos:6d}", end="\r")
        time.sleep(0.08)
    print()

running = False
th.join()
slave.output = bytes(len(slave.output))
master.send_processdata()
master.close()
print(f"\n[*] 測試流程結束，最終 SW: 0x{last_sw:04X}")
