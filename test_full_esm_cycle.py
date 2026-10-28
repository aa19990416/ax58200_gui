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

# 1. 強制重置從站至 INIT 狀態
print("\n[*] 步驟 1: 強制將從站復位回 INIT 狀態...")
slave.state = pysoem.INIT_STATE
master.write_state()
master.state_check(pysoem.INIT_STATE, 50000)
time.sleep(0.5)

# 2. 推進至 PREOP 狀態
print("[*] 步驟 2: 推進至 PREOP 狀態...")
slave.state = pysoem.PREOP_STATE
master.write_state()
master.state_check(pysoem.PREOP_STATE, 50000)
time.sleep(0.5)

# 3. 在 PREOP 階段配置 DC
cycle_time_ns = 1_000_000
try:
    slave.dc_sync(True, cycle_time_ns, cycle_time_ns)
    master.config_dc()
    print("[+] PREOP 階段: 成功配置 DC (SYNC0/SYNC1 = 1ms)")
except Exception as e:
    print(f"[!] DC 配置提示: {e}")

# 4. 配置 IO 映射 (14 Bytes)
master.config_map()

# 打包與解包
def pack_rxpdo(cw, pos=0, vel=0, mode=8):
    return struct.pack("<Hiib3x", cw, pos, vel, mode)

def unpack_txpdo(data):
    if len(data) >= 14:
        return struct.unpack("<Hiib3x", data[:14])
    return 0, 0, 0, 0

current_cw = 0x0000
last_sw = 0
running = True

def pdo_cycle():
    global last_sw, running
    while running:
        t0 = time.perf_counter()
        slave.output = pack_rxpdo(current_cw, 0, 0, 8)
        master.send_processdata()
        wkc = master.receive_processdata(2000)
        if wkc > 0 and slave.input:
            sw, _, _, _ = unpack_txpdo(slave.input)
            last_sw = sw
        elapsed = time.perf_counter() - t0
        delay = 0.001 - elapsed
        if delay > 0:
            time.sleep(delay)

# 啟動背景 PDO 發送
th = threading.Thread(target=pdo_cycle, daemon=True)
th.start()

# 5. 推進至 SAFEOP 狀態
print("\n[*] 步驟 3: 推進至 SAFEOP 狀態 (觸發 StartInputHandler)...")
slave.state = pysoem.SAFEOP_STATE
master.write_state()
master.state_check(pysoem.SAFEOP_STATE, 50000)

# 在 SAFEOP 維持資料流 1.5 秒讓 MCU 充分消化中斷
print("[*] 維持 SAFEOP 通訊 1.5 秒...")
time.sleep(1.5)

# 6. 推進至 OP 狀態
print("\n[*] 步驟 4: 推進至 OP 狀態 (觸發 StartOutputHandler)...")
slave.state = pysoem.OP_STATE
master.write_state()
master.state_check(pysoem.OP_STATE, 50000)
print(f"[+] 從站通訊狀態: {hex(slave.state)}")

# 7. 推進 CiA 402 狀態機
def sdo_r_sw():
    try:
        raw = slave.sdo_read(0x6041, 0)
        return struct.unpack("<H", raw)[0]
    except Exception:
        return 0

print("\n[*] 步驟 5: 觀察狀態字變化...")
time.sleep(0.5)
print(f"  進入 OP 初始狀態 -> PDO SW: 0x{last_sw:04X} | SDO SW: 0x{sdo_r_sw():04X}")

sequence = [
    (0x0080, 0.4, "Fault Reset (0x0080)"),
    (0x0000, 0.2, "復位清零 (0x0000)"),
    (0x0006, 0.6, "Shutdown (0x0006 -> 期待 0x1221 / 0x1240)"),
    (0x0007, 0.6, "Switch On (0x0007 -> 期待 0x1223)"),
    (0x000F, 1.5, "Enable Operation (0x000F -> 期待 0x1227)"),
]

for cw, dur, desc in sequence:
    print(f"\n[*] 執行步驟: {desc}")
    current_cw = cw
    # 同步透過 SDO 寫入 Controlword
    try:
        slave.sdo_write(0x6040, 0, struct.pack("<H", cw))
    except Exception:
        pass
    
    t_end = time.time() + dur
    while time.time() < t_end:
        print(f"    PDO SW: 0x{last_sw:04X} | SDO SW: 0x{sdo_r_sw():04X}", end="\r")
        time.sleep(0.08)
    print()

running = False
th.join()
slave.output = bytes(len(slave.output))
master.send_processdata()
master.close()
print("\n[*] 完整 ESM 循環測試結束。")
