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
last_vel = 0
last_mode = 0
last_wkc = 0
running = True

# 保持 1ms 週期收發，持續餵狗
def pdo_cycle():
    global last_sw, last_pos, last_vel, last_mode, last_wkc, running
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
            last_vel = vel
            last_mode = mode

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

# 2. 清除可能殘留的 Error Flag，確認落於 SAFEOP
cur_stat = read_u16(0x0130)
if cur_stat and (cur_stat & 0x0010):
    write_u16(0x0120, 0x0014)
    time.sleep(0.2)
write_u16(0x0120, 0x0004)
time.sleep(0.2)

# 3. 強制推入真實 OP (0x0120 = 0x0008)
write_u16(0x0120, 0x0008)
time.sleep(0.3)

real_stat = read_u16(0x0130)
print(f"[+] 從站真實狀態: 0x{real_stat:04X} ({'OP 狀態確立' if real_stat == 0x0008 else '未進入 OP'})")

# 4. 同步反饋位置
target_pos = last_pos
print(f"[+] 起始反饋對齊: 實際位置 = {target_pos}, 當前 SW = 0x{last_sw:04X}\n")

# 5. CiA 402 連續跳轉時序 (在封包流不中斷的情況下清除 0x7500 並切入使能)
steps = [
    (0x0000, 0.3, "1. 確保控制字歸零 (0x0000)"),
    (0x0080, 0.5, "2. 下發 Fault Reset (0x0080，清除 0x7500 通訊逾時故障)"),
    (0x0000, 0.3, "3. 釋放 Reset (0x0000 -> 期待脫離 Fault，SW 變為 0x1240 / 0x1250)"),
    (0x0006, 0.8, "4. 下發 Shutdown (0x0006 -> 期待 Ready to Switch On，SW 變為 0x1221)"),
    (0x0007, 0.8, "5. 下發 Switch On (0x0007 -> 期待 Switched On，SW 變為 0x1223)"),
    (0x000F, 3.0, "6. 下發 Enable Operation (0x000F -> 期待 Operation Enabled 激磁鎖軸，SW 變為 0x1227)"),
]

for cw, dur, desc in steps:
    print(f"[*] 執行步驟: {desc}")
    current_cw = cw
    target_pos = last_pos
    t_end = time.time() + dur
    while time.time() < t_end:
        print(f"    下發CW: 0x{current_cw:04X} | 反饋SW: 0x{last_sw:04X} | 實際位置: {last_pos:6d} | 速度: {last_vel:5d} | WKC: {last_wkc}", end="\r")
        time.sleep(0.05)
    print()

# 測試結束前讀取 0x603F 故障碼確認是否已清除
err_final = struct.unpack("<H", slave.sdo_read(0x603F, 0))[0]
print(f"\n[*] 執行結束後 0x603F Error Code: 0x{err_final:04X} | 最終 SW: 0x{last_sw:04X}")

running = False
th.join()
slave.output = bytes(len(slave.output))
master.send_processdata()
master.close()
