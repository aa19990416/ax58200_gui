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

master.config_map()

def pack_rxpdo(cw, pos=0, vel=0, mode=8):
    return struct.pack("<Hiib3x", cw, pos, vel, mode)

def unpack_txpdo(data):
    if len(data) >= 14:
        return struct.unpack("<Hiib3x", data[:14])
    return 0, 0, 0, 0

current_cw = 0x0000
last_sw = 0
last_wkc = 0
running = True

def pdo_cycle():
    global last_sw, last_wkc, running
    while running:
        t0 = time.perf_counter()
        slave.output = pack_rxpdo(current_cw, 0, 0, 8)
        master.send_processdata()
        wkc = master.receive_processdata(2000)
        last_wkc = wkc

        if wkc > 0 and slave.input:
            sw, pos, vel, mode = unpack_txpdo(slave.input)
            last_sw = sw

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
    slave._fpwr(addr, struct.pack("<H", val))

# 1. 推進至 SAFEOP
slave.state = pysoem.SAFEOP_STATE
master.write_state()
master.state_check(pysoem.SAFEOP_STATE, 50000)
time.sleep(0.3)

# 2. 清除殘留 Error，強制進入 OP
cur_stat = read_u16(0x0130)
if cur_stat and (cur_stat & 0x0010):
    write_u16(0x0120, 0x0014)
    time.sleep(0.2)
write_u16(0x0120, 0x0004)
time.sleep(0.2)
write_u16(0x0120, 0x0008)
time.sleep(0.3)

print(f"[+] OP 狀態確認 -> AL Status: 0x{read_u16(0x0130):04X}")

# 3. 讀取 ESC Watchdog 暫存器 (0x0400 ~ 0x0442)
wd_div  = read_u16(0x0400)
wd_pdi  = read_u16(0x0410)
wd_sm   = read_u16(0x0420)
wd_stat = read_u16(0x0440)
print("\n" + "=" * 65)
print("ESC 硬體 Watchdog 狀態 (0x0400 ~ 0x0440):")
print(f"  0x0400 Watchdog Divider : 0x{wd_div:04X}" if wd_div is not None else "  0x0400: Fail")
print(f"  0x0410 Watchdog PDI     : 0x{wd_pdi:04X}" if wd_pdi is not None else "  0x0410: Fail")
print(f"  0x0420 Watchdog SyncMgr : 0x{wd_sm:04X} ({wd_sm * 0.1:.1f} ms)" if wd_sm is not None else "  0x0420: Fail")
print(f"  0x0440 Watchdog Status  : 0x{wd_stat:04X} (Bit 0=SyncMgr WD Expired? {'YES (逾時觸發!)' if (wd_stat and wd_stat & 1) else 'NO (正常)'})")
print("=" * 65)

# 4. 測試透過 SDO 直接清除 Fault (在持續發送 1ms PDO 封包下)
print("\n[*] 正在測試直接寫入 SDO 0x6040 = 0x0080 進行強制復位...")
slave.sdo_write(0x6040, 0, struct.pack("<H", 0x0080))
time.sleep(0.1)

cw_now = struct.unpack("<H", slave.sdo_read(0x6040, 0))[0]
sw_now = struct.unpack("<H", slave.sdo_read(0x6041, 0))[0]
err_now = struct.unpack("<H", slave.sdo_read(0x603F, 0))[0]
print(f"  寫入 0x0080 後: 0x6040={cw_now:04X} | 0x6041(SW)={sw_now:04X} | 0x603F(Err)={err_now:04X}")

# 5. 釋放復位 (寫入 0x0000)
slave.sdo_write(0x6040, 0, struct.pack("<H", 0x0000))
time.sleep(0.1)
cw_now = struct.unpack("<H", slave.sdo_read(0x6040, 0))[0]
sw_now = struct.unpack("<H", slave.sdo_read(0x6041, 0))[0]
err_now = struct.unpack("<H", slave.sdo_read(0x603F, 0))[0]
print(f"  寫入 0x0000 後: 0x6040={cw_now:04X} | 0x6041(SW)={sw_now:04X} | 0x603F(Err)={err_now:04X}")

running = False
th.join()
slave.output = bytes(len(slave.output))
master.send_processdata()
master.close()
