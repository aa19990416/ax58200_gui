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

# 1. 不配置 DC，使用純輪詢，避免時鐘鎖定限制
master.config_map()

running = True
last_sw_pdo = 0

def pdo_keepalive():
    global last_sw_pdo, running
    while running:
        t0 = time.perf_counter()
        slave.output = bytes(len(slave.output))
        master.send_processdata()
        wkc = master.receive_processdata(2000)
        if wkc > 0 and slave.input and len(slave.input) >= 2:
            last_sw_pdo = struct.unpack("<H", slave.input[:2])[0]
        elapsed = time.perf_counter() - t0
        delay = 0.001 - elapsed
        if delay > 0:
            time.sleep(delay)

th = threading.Thread(target=pdo_keepalive, daemon=True)
th.start()

# 2. 推進至 OP 狀態
slave.state = pysoem.SAFEOP_STATE
master.write_state()
master.state_check(pysoem.SAFEOP_STATE, 50000)
time.sleep(0.5)

slave.state = pysoem.OP_STATE
master.write_state()
master.state_check(pysoem.OP_STATE, 50000)
print(f"[+] 從站已進入 OP 狀態: {hex(slave.state)}")

def sdo_write_cw(cw_val):
    slave.sdo_write(0x6040, 0, struct.pack("<H", cw_val))

def sdo_read_sw():
    raw = slave.sdo_read(0x6041, 0)
    return struct.unpack("<H", raw)[0]

# 先設定模式為 CSP (8)
try:
    slave.sdo_write(0x6060, 0, struct.pack("<b", 8))
    print("[+] 成功透過 SDO 設定模式 0x6060 = 8 (CSP)")
except Exception as e:
    print(f"[!] 設定模式提示: {e}")

# 3. 透過 SDO 下發標準 CiA 402 狀態機跳轉序列
sequence = [
    (0x0080, 0.4, "Fault Reset 復位 (0x0080)"),
    (0x0000, 0.2, "復位清零 (0x0000)"),
    (0x0006, 0.6, "Shutdown 命令 (0x0006 -> 期待 0x1221 或 0x1240)"),
    (0x0007, 0.6, "Switch On 命令 (0x0007 -> 期待 0x1223)"),
    (0x000F, 1.5, "Enable Operation 命令 (0x000F -> 期待 0x1227 激磁鎖軸)"),
]

print("\n[*] 開始透過 SDO 推進狀態機...")
for cw, delay, desc in sequence:
    print(f"\n[*] 執行步驟: {desc}")
    try:
        sdo_write_cw(cw)
    except Exception as e:
        print(f"    [-] SDO 寫入 0x6040 失敗: {e}")
    
    t_end = time.time() + delay
    while time.time() < t_end:
        sw_sdo = sdo_read_sw()
        print(f"    [SDO 讀取] 0x6041 SW = 0x{sw_sdo:04X} | [PDO 回傳] SW = 0x{last_sw_pdo:04X}", end="\r")
        time.sleep(0.08)
    print()

running = False
th.join()
slave.output = bytes(len(slave.output))
master.send_processdata()
master.close()
print("\n[*] SDO 狀態機測試結束。")
