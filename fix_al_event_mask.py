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

# 輔助讀寫 ESC 暫存器 (支援不同 pysoem 版本之 API)
def read_esc_reg(reg_addr, length):
    for method in ['read_reg', 'reg_read']:
        if hasattr(slave, method):
            try:
                return getattr(slave, method)(reg_addr, length)
            except Exception:
                pass
    return None

def write_esc_reg(reg_addr, data_bytes):
    for method in ['write_reg', 'reg_write']:
        if hasattr(slave, method):
            try:
                getattr(slave, method)(reg_addr, data_bytes)
                return True
            except Exception:
                pass
    return False

# 1. 檢測目前的 AL Event Mask (0x0204)
mask_bytes = read_esc_reg(0x0204, 2)
if mask_bytes:
    mask_val = struct.unpack("<H", mask_bytes)[0]
    print(f"[*] 當前 ESC AL Event Mask (0x0204): 0x{mask_val:04X}")
else:
    print("[!] 無法直接透過 slave 方法讀取暫存器，將嘗試直接注入寫入...")

# 2. 自動配置 PDO (確認為 Rx: 14B, Tx: 14B)
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

# 3. 推進至 SAFEOP 狀態
print("[*] 推進至 SAFEOP 狀態...")
slave.state = pysoem.SAFEOP_STATE
master.write_state()
master.state_check(pysoem.SAFEOP_STATE, 50000)
time.sleep(0.5)

# 4. 【關鍵修復】向 0x0204 (AL Event Mask) 寫入啟用 SM2 (Bit 8 = 0x0100) 與 SM3 (Bit 9 = 0x0200)
# 0x0300 = SM2 + SM3 中斷全開；0x0304 = 含 AL Control Event
ret = write_esc_reg(0x0204, struct.pack("<H", 0x0304))
if ret:
    print("[+] 成功將 ESC AL Event Mask (0x0204) 配置為 0x0304 (SM2/SM3 PDI 中斷解鎖)！")
else:
    print("[-] 寫入 0x0204 失敗，檢查是否有權限或 API 支援")

# 5. 推進至 OP 狀態
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
print("測試解鎖中斷後，PDO 發送 0x0006 是否搬移至 0x6040:")
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
        print(f"\n[!!! 成功突破 !!!] 0x6040 已被 PDO 覆寫為 0x{target_cw:04X}！APPL_OutputMapping 已正常運作！")
        break
        
    sample_idx += 1
    time.sleep(0.3)

running = False
th.join()
slave.output = bytes(len(slave.output))
master.send_processdata()
master.close()
print("\n[*] 測試流程結束。")
