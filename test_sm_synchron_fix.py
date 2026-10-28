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

# 1. 確保完全關閉 DC，嚴格鎖定在從站要求的 SM-Synchron 模式
master.config_map()

# 依照 0x1600 / 0x1A00 結構 (<Hiib3x)
def pack_rxpdo(cw, pos=0, vel=0, mode=8):
    return struct.pack("<Hiib3x", cw, pos, vel, mode)

def unpack_txpdo(data):
    if len(data) >= 14:
        return struct.unpack("<Hiib3x", data[:14])
    return 0, 0, 0, 0

target_cw = 0x0006  # 持續透過 PDO 發送 0x0006
last_pdo_sw = 0
last_pdo_pos = 0
last_wkc = 0
running = True

def pdo_cycle():
    global last_pdo_sw, last_pdo_pos, last_wkc, running
    while running:
        t0 = time.perf_counter()
        
        slave.output = pack_rxpdo(target_cw, last_pdo_pos, 0, 8)
        master.send_processdata()
        wkc = master.receive_processdata(2000)
        last_wkc = wkc

        if wkc > 0 and slave.input:
            sw, pos, vel, mode = unpack_txpdo(slave.input)
            last_pdo_sw = sw
            last_pdo_pos = pos

        elapsed = time.perf_counter() - t0
        delay = 0.001 - elapsed
        if delay > 0:
            time.sleep(delay)

th = threading.Thread(target=pdo_cycle, daemon=True)
th.start()

# 2. 推進至 SAFEOP 狀態
print("[*] 推進至 SAFEOP 狀態...")
slave.state = pysoem.SAFEOP_STATE
master.write_state()
master.state_check(pysoem.SAFEOP_STATE, 50000)
time.sleep(1.0)

# 3. 推進至 OP 狀態
print("[*] 推進至 OP 狀態...")
slave.state = pysoem.OP_STATE
master.write_state()
master.state_check(pysoem.OP_STATE, 50000)
print(f"[+] 從站進入狀態: {hex(slave.state)} (0x8 代表 OP)")

def sdo_read_u16(idx, sub):
    try:
        raw = slave.sdo_read(idx, sub)
        return struct.unpack("<H", raw)[0]
    except Exception:
        return None

print("\n" + "=" * 70)
print("測試在純 SM-Synchron 模式下，PDO 發送 0x0006 是否搬移至 0x6040:")
print("=" * 70)

t_end = time.time() + 4.0
sample_idx = 1

while time.time() < t_end:
    sdo_cw = sdo_read_u16(0x6040, 0)
    sdo_sw = sdo_read_u16(0x6041, 0)
    
    cw_str = f"0x{sdo_cw:04X}" if sdo_cw is not None else "讀取失敗"
    sw_str = f"0x{sdo_sw:04X}" if sdo_sw is not None else "讀取失敗"
    status = "【命中成功！】" if sdo_cw == target_cw else "【未搬移】"
    
    print(f"[{sample_idx:02d}] {status} | PDO發送: 0x{target_cw:04X} | SDO讀出0x6040: {cw_str} | SW: {sw_str} | WKC: {last_wkc}")
    
    if sdo_cw == target_cw:
        print(f"\n[!!! 成功突破 !!!] SM-Synchron 模式下 APPL_OutputMapping 正常運作！0x6040 已更新為 0x{target_cw:04X}！")
        break
        
    sample_idx += 1
    time.sleep(0.3)

running = False
th.join()
slave.output = bytes(len(slave.output))
master.send_processdata()
master.close()
print("\n[*] 測試流程結束。")
