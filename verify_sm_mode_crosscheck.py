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

# 【核心】：不啟用 DC 同步，退回 SM-Synchron 模式
master.config_map()

# 依照 0x1600 / 0x1A00 結構 (<Hiib3x)
def pack_rxpdo(cw, pos=0, vel=0, mode=8):
    return struct.pack("<Hiib3x", cw, pos, vel, mode)

def unpack_txpdo(data):
    if len(data) >= 14:
        return struct.unpack("<Hiib3x", data[:14])
    return 0, 0, 0, 0

target_cw = 0x0006  # 持續透過 PDO 發送 Shutdown (0x0006)
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

# 推進至 SAFEOP 狀態
print("[*] 推進至 SAFEOP 狀態...")
slave.state = pysoem.SAFEOP_STATE
master.write_state()
master.state_check(pysoem.SAFEOP_STATE, 50000)
time.sleep(0.5)

# 推進至 OP 狀態
print("[*] 推進至 OP 狀態...")
slave.state = pysoem.OP_STATE
master.write_state()
master.state_check(pysoem.OP_STATE, 50000)
print(f"[+] 從站成功進入狀態: {hex(slave.state)} (0x8 代表 OP)")

def sdo_read_u16(idx, sub):
    try:
        raw = slave.sdo_read(idx, sub)
        return struct.unpack("<H", raw)[0]
    except Exception:
        return None

print("\n" + "=" * 75)
print(f"[*] 開始 SM 模式下 PDO vs SDO 交叉驗證 (持續 5 秒，PDO 持續發送 CW = 0x{target_cw:04X}):")
print("=" * 75)

t_end = time.time() + 5.0
sample_idx = 1

while time.time() < t_end:
    sdo_cw = sdo_read_u16(0x6040, 0)
    sdo_sw = sdo_read_u16(0x6041, 0)
    
    if sdo_cw is not None:
        sdo_cw_str = f"0x{sdo_cw:04X}"
        hit_status = "【命中成功】" if sdo_cw == target_cw else "【未搬移】"
    else:
        sdo_cw_str = "讀取失敗"
        hit_status = "【SDO異常】"

    sdo_sw_str = f"0x{sdo_sw:04X}" if sdo_sw is not None else "讀取失敗"
    
    print(f"[{sample_idx:02d}] {hit_status} | PDO發送CW: 0x{target_cw:04X} | SDO讀出0x6040(CW): {sdo_cw_str} | SDO讀出0x6041(SW): {sdo_sw_str} | PDO回傳SW: 0x{last_pdo_sw:04X} | WKC: {last_wkc}")
    sample_idx += 1
    time.sleep(0.3)

running = False
th.join()
slave.output = bytes(len(slave.output))
master.send_processdata()
master.close()
print("\n[*] 交叉驗證測試結束。")
