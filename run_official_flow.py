import pysoem
import struct
import time
import math
import threading
import sys

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到 EtherCAT 從站！")
    exit()

slave = master.slaves[0]
print(f"[+] 找到從站: {slave.name}")

master.config_map()

# 打包 RxPDO (14 Bytes: CW, TargetPos, TargetVel, Mode, 3-byte pad)
def pack_rxpdo(cw, pos=0, vel=0, mode=6):
    return struct.pack("<Hiib3x", cw, pos, vel, mode)

# 解包 TxPDO (14 Bytes: SW, ActPos, ActVel, ActMode, 3-byte pad)
def unpack_txpdo(data):
    if len(data) >= 14:
        return struct.unpack("<Hiib3x", data[:14])
    return 0, 0, 0, 0

current_cw = 0x0000
target_pos = 0
target_vel = 0
current_mode = 6  # 依官方流程：起始設定為 Mode 6 (Homing)

last_sw = 0
actual_pos = 0
actual_vel = 0
actual_mode = 0
last_wkc = 0
running = True

# 1ms 週期性 PDO 通訊執行緒
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
print(f"[+] 從站 OP 狀態確認: 0x{read_u16(0x0130):04X} | 起始位置: {actual_pos}")

# =====================================================================
# 步驟 1: Servo-On (使能激磁)
# =====================================================================
print("\n" + "=" * 65)
print("[步驟 1] 執行 Servo-On...")
print("=" * 65)

for cw, expect in [(0x0006, 0x0021), (0x0007, 0x0023), (0x000F, 0x0027)]:
    current_cw = cw
    target_pos = actual_pos
    t_wait = time.time() + 1.5
    while time.time() < t_wait:
        if (last_sw & 0x006F) == expect:
            break
        time.sleep(0.02)

if (last_sw & 0x006F) != 0x0027:
    print(f"[-] Servo-On 失敗，SW: 0x{last_sw:04X}")
    running = False
    th.join()
    exit()

print(f"[+] Servo-On 成功！SW = 0x{last_sw:04X} (激磁鎖軸中)")

# =====================================================================
# 步驟 2: Homing 回原點流程 (Mode 6)
# =====================================================================
print("\n" + "=" * 65)
print("[步驟 2] 執行 Homing 回原點標定 (Mode 6)...")
print("=" * 65)

current_mode = 6
current_cw = 0x000F
time.sleep(0.2)

# 觸發 Homing 運轉 (CW = 0x001F)
print("[*] 觸發 Homing 運轉 (下發 CW = 0x001F)...")
current_cw = 0x001F

t_start = time.time()
homing_ok = False
while time.time() - t_start < 8.0:
    is_attained = bool(last_sw & 0x1000)
    is_error = bool(last_sw & 0x2000)
    sys.stdout.write(f"\r  [Homing 中] SW: 0x{last_sw:04X} | 位置: {actual_pos:8d} | Homing 完成: {is_attained}")
    sys.stdout.flush()
    if is_attained:
        homing_ok = True
        break
    if is_error:
        print(f"\n[-] Homing 回報錯誤，SW: 0x{last_sw:04X}")
        break
    time.sleep(0.05)
print()

if not homing_ok:
    print("[-] Homing 未能順利完成，停止後續 CSP 流程。")
    running = False
    th.join()
    exit()

# 結束 Homing 運轉觸發信號
current_cw = 0x000F
time.sleep(0.3)
home_origin = actual_pos
target_pos = home_origin
print(f"[+] Homing 成功標定！原點基準位置: {home_origin}")

# =====================================================================
# 步驟 3: 切換至 CSP 模式 (Mode 8)
# =====================================================================
print("\n" + "=" * 65)
print("[步驟 3] 切換至 CSP 模式 (Mode 8)...")
print("=" * 65)

current_mode = 8
target_pos = actual_pos
time.sleep(0.3)

# 檢查回饋模式 0x6061
t_wait_mode = time.time() + 1.0
while time.time() < t_wait_mode:
    if actual_mode == 8:
        break
    time.sleep(0.05)

print(f"[+] 模式切換檢查 -> 反饋模式 (0x6061): {actual_mode} ({'CSP 就緒！' if actual_mode == 8 else '注意: 模式尚未切換至 8'})")

# =====================================================================
# 步驟 4: CSP 運動控制測試
# =====================================================================
def move_csp(target, duration):
    global target_pos
    start = target_pos
    steps = int(duration / 0.005)
    for i in range(steps + 1):
        s = i / steps
        # 5次多項式平滑內插
        scale = 10 * (s ** 3) - 15 * (s ** 4) + 6 * (s ** 5)
        target_pos = int(start + (target - start) * scale)
        diff = target_pos - actual_pos
        sys.stdout.write(f"\r  [CSP 運動] 目標: {target_pos:7d} | 實際: {actual_pos:7d} | 誤差: {diff:4d} | 速度: {actual_vel:5d}")
        sys.stdout.flush()
        time.sleep(0.005)
    print()

try:
    print("\n" + "=" * 65)
    print("[步驟 4] 開始執行 CSP 運動追隨驗證...")
    print("=" * 65)

    print("[*] 1. 正轉 +2000 counts...")
    move_csp(home_origin + 2000, 1.5)
    time.sleep(0.5)

    print("[*] 2. 返回原點...")
    move_csp(home_origin, 1.5)
    time.sleep(0.5)

    print("[*] 3. 正轉一整圈 (+10000 counts)...")
    move_csp(home_origin + 10000, 2.5)
    time.sleep(0.5)

    print("[*] 4. 反轉回原點...")
    move_csp(home_origin, 2.5)
    print("\n[+] 官方標準流程運動測試全部完成！")

except KeyboardInterrupt:
    print("\n[*] 使用者中斷測試。")

finally:
    print("[*] 正在退使能並關閉 EtherCAT 連線...")
    current_cw = 0x0000
    time.sleep(0.2)
    running = False
    th.join()
    slave.output = bytes(len(slave.output))
    master.send_processdata()
    master.close()
    print("[*] 伺服已安全斷電釋放。")
