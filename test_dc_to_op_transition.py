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

# 2. 推進至 SAFEOP
slave.state = pysoem.SAFEOP_STATE
master.write_state()
master.state_check(pysoem.SAFEOP_STATE, 50000)
time.sleep(1.0)
print(f"[*] 進入 SAFEOP 後 -> 0x0130 (AL Status): 0x{read_u16(0x0130):04X}")

# 3. 推進至 OP
print("[*] 發送 OP 請求...")
slave.state = pysoem.OP_STATE
master.write_state()
# 等待握手
chk_state = master.state_check(pysoem.OP_STATE, 50000)

time.sleep(0.5)

al_ctrl = read_u16(0x0120)
al_stat = read_u16(0x0130)
al_stat_code = read_u16(0x0134)
al_event_mask = read_u16(0x0204)
al_event_req = read_u16(0x0220)

print("\n" + "=" * 65)
print(f"Master state_check 結果 : 0x{chk_state:02X}")
print(f"0x0120 AL Control (請求) : 0x{al_ctrl:04X}")
print(f"0x0130 AL Status  (實際) : 0x{al_stat:04X} ({'成功進入 OP!' if al_stat == 0x0008 else '仍然卡在非 OP 狀態'})")
print(f"0x0134 AL Status Code    : 0x{al_stat_code:04X}")
print(f"0x0204 AL Event Mask     : 0x{al_event_mask:04X}")
print(f"0x0220 AL Event Request  : 0x{al_event_req:04X}")
print(f"即時 WKC                 : {last_wkc}")
print("=" * 65)

# 4. 交叉讀取 0x6040
try:
    cw_6040 = struct.unpack("<H", slave.sdo_read(0x6040, 0))[0]
    print(f"SDO 讀取 0x6040(CW)      : 0x{cw_6040:04X}")
except Exception as e:
    print(f"SDO 讀取 0x6040 失敗: {e}")

running = False
th.join()
slave.output = bytes(len(slave.output))
master.send_processdata()
master.close()
