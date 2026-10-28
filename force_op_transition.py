import pysoem
import struct
import time
import threading

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到從站！")
    exit()

slave = master.slaves[0]
print(f"[+] 找到從站: {slave.name}")

# 1. 啟用 DC 同步 (1ms)
cycle_time_ns = 1_000_000
try:
    slave.dc_sync(True, cycle_time_ns, cycle_time_ns)
    master.config_dc()
    print("[+] Master 已配置 DC 模式 (1ms)")
except Exception as e:
    print(f"[!] DC 配置提示: {e}")

master.config_map()

# 打包 14-byte 封包 (CW = 0x0006 Shutdown)
def pack_rxpdo(cw=0x0006, pos=0, vel=0, mode=8):
    return struct.pack("<Hiib3x", cw, pos, vel, mode)

running = True
last_wkc = 0

def pdo_cycle():
    global last_wkc, running
    while running:
        t0 = time.perf_counter()
        slave.output = pack_rxpdo(0x0006, 0, 0, 8)
        master.send_processdata()
        wkc = master.receive_processdata(2000)
        last_wkc = wkc
        elapsed = time.perf_counter() - t0
        delay = 0.001 - elapsed
        if delay > 0:
            time.sleep(delay)

th = threading.Thread(target=pdo_cycle, daemon=True)
th.start()

def read_u16(addr):
    try:
        raw = slave._fprd(addr, 2)
        return struct.unpack("<H", raw)[0]
    except Exception:
        return None

def write_u16(addr, val):
    try:
        slave._fpwr(addr, struct.pack("<H", val))
        return True
    except Exception:
        return False

# 2. 推進至 SAFEOP
slave.state = pysoem.SAFEOP_STATE
master.write_state()
master.state_check(pysoem.SAFEOP_STATE, 50000)
time.sleep(1.0)
print(f"[*] SAFEOP 穩定階段 -> 0x0130 AL Status: 0x{read_u16(0x0130):04X}")

# 3. 【關鍵突破】：直接透過 _fpwr 強制對 0x0120 寫入 0x0008 (OP 請求)
print("[*] 正在透過 _fpwr 強制對 0x0120 (AL Control) 寫入 0x0008 (OP 請求)...")
write_u16(0x0120, 0x0008)

# 持續監控 3 秒
print("\n" + "=" * 70)
print("監控狀態轉移與 0x6040 數值變化:")
print("=" * 70)

for i in range(1, 11):
    time.sleep(0.3)
    al_ctrl = read_u16(0x0120)
    al_stat = read_u16(0x0130)
    al_stat_code = read_u16(0x0134)
    al_event_req = read_u16(0x0220)
    
    try:
        sdo_cw = struct.unpack("<H", slave.sdo_read(0x6040, 0))[0]
        cw_str = f"0x{sdo_cw:04X}"
    except Exception:
        sdo_cw = None
        cw_str = "讀取失敗"
        
    status_label = "【成功進 OP!】" if (al_stat and (al_stat & 0x0F) == 0x08) else "【尚未進 OP】"
    print(f"[{i:02d}] {status_label} AL Control: 0x{al_ctrl:04X} | AL Status: 0x{al_stat:04X} | ErrorCode: 0x{al_stat_code:04X} | EventReq: 0x{al_event_req:04X} | 0x6040: {cw_str} | WKC: {last_wkc}")

running = False
th.join()
slave.output = bytes(len(slave.output))
master.send_processdata()
master.close()
print("\n[*] 測試完成。")
