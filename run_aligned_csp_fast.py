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

# 提高搜尋速度，確保 5 秒內必定能找到 Z 相 (一圈 10000 counts)
try:
    slave.sdo_write(0x6099, 1, struct.pack("<I", 2500))  # 0.25 圈/秒
    slave.sdo_write(0x6099, 2, struct.pack("<I", 1500))  # 0.15 圈/秒
    print("[+] 成功將 0x6099 尋零速度提升為 2500 / 1500 counts/s")
except Exception as e:
    print(f"[*] SDO 配置: {e}")

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
print("[步驟 1] 激磁使能 (Mode 6: Homing)...")
print("=" * 65)

# 清除殘留 Fault
if (last_sw & 0x0008) != 0:
    current_cw = 0x0080
    time.sleep(0.2)
    current_cw = 0x0000
    time.sleep(0.1)

for cw in [0x0006, 0x0007, 0x000F]:
    current_cw = cw
    target_pos = actual_pos
    time.sleep(0.2)

print(f"[+] 激磁完成！SW = 0x{last_sw:04X} | 模式: {actual_mode}")

print("\n" + "=" * 65)
print("[步驟 2] 觸發電角度對齊與 Z 相搜尋 (下發 CW = 0x001F)...")
print("=" * 65)

current_cw = 0x001F

# 等待驅動器開始響應
t_start = time.time()
while time.time() - t_start < 2.0:
    if (last_sw & 0x1000) == 0:
        print(f"[+] 驅動器確認開始旋轉搜尋 Z 相！SW = 0x{last_sw:04X}")
        break
    time.sleep(0.01)

# 等待對齊與 Z 相捕捉完成 (最多 5 秒)
t_wait = time.time()
aligned = False
while time.time() - t_wait < 5.0:
    attained = bool(last_sw & 0x1000)
    reached = bool(last_sw & 0x0400)
    error = bool(last_sw & 0x2000)
    sys.stdout.write(f"\r  [搜尋中] SW: 0x{last_sw:04X} | 位置: {actual_pos:6d} | 速度: {actual_vel:4d} | Attained: {attained} | Reached: {reached}")
    sys.stdout.flush()
    if attained and reached:
        aligned = True
        print(f"\n\n[!!! 完美對齊 !!!] 電角度與 Z 相關節零點校正完成！原點位置: {actual_pos}")
        break
    if error:
        print(f"\n\n[-] 對齊報錯！SW: 0x{last_sw:04X}")
        break
    time.sleep(0.02)

if not aligned:
    print("\n[-] 搜尋未在時限內完成，安全退出。")
    current_cw = 0x0000
    running = False
    th.join()
    exit()

# 結束 Homing 運轉
current_cw = 0x000F
time.sleep(0.3)

print("\n" + "=" * 65)
print("[步驟 3] 卸載 Homing -> 載入 CSP 模式 (Mode 8)...")
print("=" * 65)

# 降使能進入 Shutdown (觸發 CIA402HM_DeInit)
current_cw = 0x0006
time.sleep(0.15)

# 切換模式為 8
current_mode = 8
target_pos = actual_pos
time.sleep(0.15)

# 重新激磁進入 Operation Enabled (觸發 CIA402CSP_Init)
current_cw = 0x0007
time.sleep(0.1)
current_cw = 0x000F
time.sleep(0.3)
target_pos = actual_pos

print(f"[+] CSP 初始化完成！SW = 0x{last_sw:04X} | 模式: {actual_mode} | 零點基準: {actual_pos}")

print("\n" + "=" * 65)
print("[步驟 4] 驗證 CSP 閉環剛性與平滑移動 (+500 counts)...")
print("=" * 65)

try:
    base = actual_pos
    dur = 2.0
    steps = int(dur / 0.001)
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
    print("[+] 運轉完成，保持鎖定 3 秒（此時軸心應具備堅硬閉環剛性）...")
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
