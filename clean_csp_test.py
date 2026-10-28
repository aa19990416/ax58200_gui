import pysoem
import struct
import time
import threading
import sys

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到 EtherCAT 從站！")
    exit()

slave = master.slaves[0]
print(f"[+] 找到從站: {slave.name}")

# 不做任何 SDO 亂寫，直接配 mapping
master.config_map()

# 打包 RxPDO (14 Bytes: CW, TargetPos, TargetVel, Mode, 3-byte pad)
def pack_rxpdo(cw, pos=0, vel=0, mode=6):
    return struct.pack("<Hiib3x", cw, pos, vel, mode)

def unpack_txpdo(data):
    if len(data) >= 14:
        return struct.unpack("<Hiib3x", data[:14])
    return 0, 0, 0, 0

current_cw = 0x0000
target_pos = 0
target_vel = 0
current_mode = 6

last_sw = 0
actual_pos = 0
actual_vel = 0
actual_mode = 0
last_wkc = 0
running = True

def pdo_loop():
    global last_sw, actual_pos, actual_vel, actual_mode, last_wkc, running
    while running:
        t0 = time.perf_counter()
        slave.output = pack_rxpdo(current_cw, target_pos, target_vel, current_mode)
        master.send_processdata()
        wkc = master.receive_processdata(2000)
        last_wkc = wkc

        if wkc > 0 and slave.input:
            sw, pos, vel, mode = unpack_txpdo(slave.input)
            last_sw = sw
            actual_pos = pos
            actual_vel = vel
            actual_mode = mode

        elapsed = time.perf_counter() - t0
        delay = 0.001 - elapsed
        if delay > 0:
            time.sleep(delay)

th = threading.Thread(target=pdo_loop, daemon=True)
th.start()

def read_u16(addr):
    try:
        return struct.unpack("<H", slave._fprd(addr, 2))[0]
    except Exception:
        return None

def write_u16(addr, val):
    slave._fpwr(addr, struct.pack("<H", val))

# 推進至 OP 狀態
slave.state = pysoem.SAFEOP_STATE
master.write_state()
master.state_check(pysoem.SAFEOP_STATE, 50000)
time.sleep(0.1)

cur_stat = read_u16(0x0130)
if cur_stat and (cur_stat & 0x0010):
    write_u16(0x0120, 0x0014)
    time.sleep(0.1)
write_u16(0x0120, 0x0004)
time.sleep(0.1)
write_u16(0x0120, 0x0008)
time.sleep(0.2)

time.sleep(0.1)
target_pos = actual_pos
print(f"[+] OP 狀態確立: 0x{read_u16(0x0130):04X} | 起始位置: {actual_pos}")

# 步驟 1: Homing 模式 Servo-On
print("\n[*] 步驟 1: 以 Mode 6 激磁使能...")
for cw in [0x0006, 0x0007, 0x000F]:
    current_cw = cw
    target_pos = actual_pos
    time.sleep(0.2)

print(f"[+] 使能完成！SW = 0x{last_sw:04X}")

# 步驟 2: 觸發 Homing
print("[*] 步驟 2: 觸發 Homing 回原點...")
current_cw = 0x001F
t_end = time.time() + 6.0
while time.time() < t_end:
    if last_sw & 0x1000:
        break
    time.sleep(0.05)
print(f"[+] Homing 完成！SW = 0x{last_sw:04X} | 原點位置: {actual_pos}")

# 步驟 3: 降使能切換至 CSP (Mode 8)
print("[*] 步驟 3: 降使能換模式至 CSP (Mode 8)...")
current_cw = 0x0006
time.sleep(0.15)
current_mode = 8
target_pos = actual_pos
time.sleep(0.15)

# 等待模式變更為 8
t_m = time.time() + 1.5
while time.time() < t_m:
    if actual_mode == 8:
        break
    time.sleep(0.02)

# 重新激磁鎖軸
current_cw = 0x0007
time.sleep(0.1)
current_cw = 0x000F
time.sleep(0.2)

print(f"[!!! 激磁就緒 !!!] SW = 0x{last_sw:04X} | 模式: {actual_mode} | 當前位置: {actual_pos}")
print("=" * 65)
print("現在馬達已進入【常駐鎖軸狀態】！")
print("1. 請先用手稍微轉動馬達軸，確認是否有強烈電磁鎖定阻力。")
print("2. 按下鍵盤 [Enter] 鍵，開始慢慢向正方向以階躍累積送位置。")
print("=" * 65)

input("\n請確認軸心有上電鎖定後，按 [Enter] 開始測試位置變動...")

try:
    base = actual_pos
    print("\n[*] 開始連續給予位置斜坡 (每秒 +2000 counts)...")
    t_start = time.time()
    while time.time() - t_start < 6.0:
        elapsed = time.time() - t_start
        # 持續勻速增加目標位置
        target_pos = int(base + elapsed * 2000)
        target_vel = 2000  # 同步給予速度前饋
        
        diff = target_pos - actual_pos
        sys.stdout.write(f"\r  [斜坡驅動] 目標: {target_pos:7d} | 實際: {actual_pos:7d} | 誤差: {diff:5d} | 速度反饋: {actual_vel:4d}")
        sys.stdout.flush()
        time.sleep(0.01)
    print("\n\n[+] 測試完成，停在當前位置保持鎖定 3 秒...")
    time.sleep(3.0)

except KeyboardInterrupt:
    print("\n[*] 使用者中斷。")

finally:
    current_cw = 0x0000
    time.sleep(0.2)
    running = False
    th.join()
    slave.output = bytes(len(slave.output))
    master.send_processdata()
    master.close()
    print("[*] 安全斷開連線。")
