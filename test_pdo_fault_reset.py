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

# 打包 14-byte 封包 (<Hiib3x)
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
last_wkc = 0
running = True

# 1ms 背景 PDO 週期通訊
def pdo_cycle():
    global last_sw, last_pos, last_wkc, running
    while running:
        t0 = time.perf_counter()
        slave.output = pack_rxpdo(current_cw, target_pos, 0, 8)
        master.send_processdata()
        wkc = master.receive_processdata(2000)
        last_wkc = wkc

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
time.sleep(0.2)

# 2. 清除可能殘留的 Error Flag，確認落於 SAFEOP
cur_stat = read_u16(0x0130)
if cur_stat and (cur_stat & 0x0010):
    write_u16(0x0120, 0x0014)
    time.sleep(0.1)
write_u16(0x0120, 0x0004)
time.sleep(0.1)

# 3. 強制推入真實 OP (0x0120 = 0x0008)
write_u16(0x0120, 0x0008)
time.sleep(0.2)
real_stat = read_u16(0x0130)
print(f"[+] 從站真實狀態: 0x{real_stat:04X} ({'OP 狀態確立' if real_stat == 0x0008 else '未進入 OP'})")

# 對齊起始反饋位置
target_pos = last_pos
print(f"[+] 起始狀態: SW = 0x{last_sw:04X}\n")

# 4. 階段 1：PDO 先下發 CW = 0x0000 維持 300ms (確保狀態機看到 0 -> 1 上升沿前置信號)
print("[*] 階段 1: PDO 持續發送 CW = 0x0000 (維持 300ms)...")
current_cw = 0x0000
time.sleep(0.3)

# 5. 階段 2：PDO 持續下發 CW = 0x0080 (Fault Reset) 維持 600ms
print("[*] 階段 2: PDO 持續發送 CW = 0x0080 (Fault Reset，維持 600ms)...")
current_cw = 0x0080

print("=" * 80)
print(f"{'取樣':<6} | {'PDO 下發 CW':<14} | {'SDO 讀 0x6040':<14} | {'0x6041 (SW)':<14} | {'0x603F (Err)':<14} | {'WKC':<5}")
print("=" * 80)

t_start = time.time()
sample_idx = 1
while time.time() - t_start < 0.6:
    time.sleep(0.1)
    try:
        cw_read = struct.unpack("<H", slave.sdo_read(0x6040, 0))[0]
        cw_str = f"0x{cw_read:04X}"
    except Exception:
        cw_str = "Fail"

    try:
        err_read = struct.unpack("<H", slave.sdo_read(0x603F, 0))[0]
        err_str = f"0x{err_read:04X}"
    except Exception:
        err_str = "Fail"

    sw_str = f"0x{last_sw:04X}"
    print(f"#{sample_idx:02d}    | 0x{current_cw:04X}         | {cw_str:<14} | {sw_str:<14} | {err_str:<14} | {last_wkc:<5}")
    sample_idx += 1

# 6. 階段 3：PDO 釋放復位，下發 CW = 0x0000 維持 300ms
print("\n[*] 階段 3: PDO 釋放復位，持續發送 CW = 0x0000 (維持 300ms)...")
current_cw = 0x0000
time.sleep(0.3)

try:
    final_cw = struct.unpack("<H", slave.sdo_read(0x6040, 0))[0]
    final_err = struct.unpack("<H", slave.sdo_read(0x603F, 0))[0]
    print(f"[+] 最終結果 -> 0x6040: 0x{final_cw:04X} | 0x6041 (SW): 0x{last_sw:04X} | 0x603F (Err): 0x{final_err:04X}")
except Exception:
    pass

running = False
th.join()
slave.output = bytes(len(slave.output))
master.send_processdata()
master.close()
print("\n[*] 測試流程結束。")
