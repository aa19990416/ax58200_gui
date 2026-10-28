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

slave.state = pysoem.PREOP_STATE
master.write_state()
master.state_check(pysoem.PREOP_STATE, 50000)
time.sleep(0.1)

# 依官方配置：Home Offset = 0
try:
    slave.sdo_write(0x607C, 0, struct.pack("<i", 0))
    # 寫入適中的原點搜尋速度 (避免超速報錯)
    slave.sdo_write(0x6099, 1, struct.pack("<I", 300))
    slave.sdo_write(0x6099, 2, struct.pack("<I", 100))
    print("[+] 成功同步原廠 Home Offset = 0 與搜尋速度")
except Exception as e:
    print(f"[*] SDO 提示: {e}")

master.config_map()

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
running = True

def pdo_loop():
    global last_sw, actual_pos, actual_vel, actual_mode, running
    while running:
        t0 = time.perf_counter()
        slave.output = pack_rxpdo(current_cw, target_pos, target_vel, current_mode)
        master.send_processdata()
        wkc = master.receive_processdata(2000)

        if wkc > 0 and slave.input:
            sw, pos, vel, mode = unpack_txpdo(slave.input)
            last_sw = sw
            actual_pos = pos
            actual_vel = vel
            actual_mode = mode

        delay = 0.001 - (time.perf_counter() - t0)
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
print(f"[+] OP 狀態確認: 0x{read_u16(0x0130):04X} | 起始位置: {actual_pos}")

print("\n" + "=" * 65)
print("[原廠流程 1] 按下 Servo ON/OFF 按鈕 (使能激磁)...")
print("=" * 65)

if (last_sw & 0x0008) != 0:
    current_cw = 0x0080
    time.sleep(0.2)
    current_cw = 0x0000
    time.sleep(0.1)

for cw in [0x0006, 0x0007, 0x000F]:
    current_cw = cw
    target_pos = actual_pos
    time.sleep(0.2)

print(f"[+] Servo-ON 成功！SW = 0x{last_sw:04X} | 模式: {actual_mode}")

print("\n" + "=" * 65)
print("[原廠流程 2] 點擊 Homing 按鈕 (開始轉動回原點)...")
print("=" * 65)

current_cw = 0x001F

# 階段 1: 等待出發 (homing_attained 清零)
t_start = time.time()
while time.time() - t_start < 2.0:
    if (last_sw & 0x1000) == 0:
        print(f"[+] 馬達開始旋轉尋找原點！SW = 0x{last_sw:04X}")
        break
    time.sleep(0.01)

# 階段 2: 監聽馬達旋轉到定位
t_wait = time.time()
homed = False
while time.time() - t_wait < 15.0:
    attained = bool(last_sw & 0x1000)
    reached = bool(last_sw & 0x0400)
    error = bool(last_sw & 0x2000)
    sys.stdout.write(f"\r  [Homing 旋轉中] SW: 0x{last_sw:04X} | 實際位置: {actual_pos:6d} | 轉速: {actual_vel:4d} | Attained: {attained} | Reached: {reached}")
    sys.stdout.flush()
    if attained and (reached or actual_vel == 0):
        homed = True
        print(f"\n\n[!!! Homing 完成 !!!] 馬達已精確停在原點！標定原點位置: {actual_pos}")
        break
    if error:
        print(f"\n\n[-] Homing 報錯！SW: 0x{last_sw:04X}")
        break
    time.sleep(0.02)

# 結束 Homing 觸發信號
current_cw = 0x000F
time.sleep(0.5)

if not homed:
    print("[-] Homing 未能順利完成，請觀察上述數值。")
    current_cw = 0x0000
    running = False
    th.join()
    exit()

print("\n" + "=" * 65)
print("[原廠流程 3] 按下 CSP ON 開關 (切入 CSP 連續軌跡模式)...")
print("=" * 65)

# 降使能進入 Shutdown (卸載 Homing 介面)
current_cw = 0x0006
time.sleep(0.2)

# 切換為 Mode 8
current_mode = 8
target_pos = actual_pos
time.sleep(0.2)

# 重新激磁進入 CSP (載入 CSP 介面)
current_cw = 0x0007
time.sleep(0.1)
current_cw = 0x000F
time.sleep(0.3)
target_pos = actual_pos

print(f"[+] CSP 模式已就緒！SW = 0x{last_sw:04X} | 模式: {actual_mode} | 當前位置: {actual_pos}")

print("\n" + "=" * 65)
print("[原廠流程 4] 正式運轉：正反向各平滑轉動 500 counts")
print("=" * 65)

try:
    base = actual_pos
    dur = 2.0
    steps = int(dur / 0.001)
    
    print("[*] 1. 正轉 +500 counts...")
    t_start = time.perf_counter()
    for i in range(steps + 1):
        s = i / float(steps)
        scale = 10 * (s ** 3) - 15 * (s ** 4) + 6 * (s ** 5)
        target_pos = int(base + 500 * scale)
        diff = target_pos - actual_pos
        sys.stdout.write(f"\r  [CSP 閉環] 目標: {target_pos:6d} | 實際: {actual_pos:6d} | 誤差: {diff:4d} | 速度: {actual_vel:4d}")
        sys.stdout.flush()
        time.sleep(0.001)
    print()
    time.sleep(0.5)

    print("[*] 2. 返回原點...")
    t_start = time.perf_counter()
    for i in range(steps + 1):
        s = i / float(steps)
        scale = 10 * (s ** 3) - 15 * (s ** 4) + 6 * (s ** 5)
        target_pos = int(base + 500 - 500 * scale)
        diff = target_pos - actual_pos
        sys.stdout.write(f"\r  [CSP 閉環] 目標: {target_pos:6d} | 實際: {actual_pos:6d} | 誤差: {diff:4d} | 速度: {actual_vel:4d}")
        sys.stdout.flush()
        time.sleep(0.001)
    print()
    print("\n[+] 官方完整流程測試成功！保持鎖軸 3 秒...")
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
    print("[*] 伺服已安全斷電釋放。")
