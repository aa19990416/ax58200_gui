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

# 1. 讀取當前錯誤快照
cur_stat = read_u16(0x0130)
cur_code = read_u16(0x0134)
print(f"[*] 起始狀態快照 -> AL Status: 0x{cur_stat:04X} | AL Status Code: 0x{cur_code:04X}")

# 2. 如果帶有 Error Flag (Bit 4)，發送 Error Acknowledge
if cur_stat & 0x0010:
    print("[*] 偵測到 Error Flag (0x10)！正在下發 Error Acknowledge (0x0120 = 0x0014)...")
    write_u16(0x0120, 0x0014)
    time.sleep(0.3)
    
    ack_stat = read_u16(0x0130)
    ack_code = read_u16(0x0134)
    print(f"[+] 復位後狀態 -> AL Status: 0x{ack_stat:04X} | AL Status Code: 0x{ack_code:04X}")

# 3. 確保落於乾淨的 SAFEOP (0x0004)
write_u16(0x0120, 0x0004)
time.sleep(0.5)
clean_stat = read_u16(0x0130)
print(f"[+] 清除確認 -> AL Status: 0x{clean_stat:04X} (期望 0x0004)")

# 4. 強制請求進入 OP (0x0120 = 0x0008)
print("\n[*] 正在強制下發 OP 請求 (0x0120 = 0x0008)...")
write_u16(0x0120, 0x0008)

print("\n" + "=" * 80)
print(f"{'取樣':<6} | {'0x0120 (AL Ctrl)':<18} | {'0x0130 (AL Stat)':<18} | {'0x0134 (AL Code)':<18} | {'0x6040 (SDO)':<12} | {'WKC':<5}")
print("=" * 80)

for i in range(1, 11):
    time.sleep(0.3)
    ctrl = read_u16(0x0120)
    stat = read_u16(0x0130)
    code = read_u16(0x0134)
    
    try:
        raw_6040 = slave.sdo_read(0x6040, 0)
        cw_6040 = struct.unpack("<H", raw_6040)[0]
        cw_str = f"0x{cw_6040:04X}"
    except Exception:
        cw_str = "Fail"

    print(f"#{i:02d}    | 0x{ctrl:04X}             | 0x{stat:04X}             | 0x{code:04X}             | {cw_str:<12} | {last_wkc:<5}")
    
    if stat == 0x0008 and cw_str == "0x0006":
        print(f"\n[!!! 突破成功 !!!] 從站成功躍遷進入 OP (0x0008)，且 0x6040 已被 PDO 覆寫為 0x0006！")
        break

running = False
th.join()
slave.output = bytes(len(slave.output))
master.send_processdata()
master.close()
print("\n[*] 測試流程結束。")
