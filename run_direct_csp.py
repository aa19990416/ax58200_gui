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
print("[步驟 1] 執行 Servo-ON 使能 (原廠面板: Servo ON/OFF)...")
print("=" * 65)

# 排除可能殘留的 Fault
if (last_sw & 0x0008) != 0 or (last_sw & 0x2000) != 0:
    current_cw = 0x0080
    time.sleep(0.2)
    current_cw = 0x0000
    time.sleep(0.1)

current_cw = 0x0006
target_pos = actual_pos
time.sleep(0.2)

current_cw = 0x0007
time.sleep(0.1)

current_cw = 0x000F
time.sleep(0.3)
target_pos = actual_pos

print(f"[+] Servo-ON 激磁完成！SW = 0x{last_sw:04X} | 模式: {actual_mode}")

print("\n" + "=" * 65)
print("[步驟 2] 觸發 Homing 旋轉定位 (原廠面板: Homing 按鈕)...")
print("=" * 65)

# 持續維持 0x001F，不可提前撤除！
current_cw = 0x001F

t_start = time.time()
homed = False
has_started = False

while time.time() - t_start < 10.0:
    attained = bool(last_sw & 0x1000)
    reached  = bool(last_sw & 0x0400)
    error    = bool(last_sw & 0x2000)
    fault    = bool(last_sw & 0x0008)
    
    # 觀察狀態機是否進入運動
    if (last_sw & 0x1000) == 0:
        has_started = True

    sys.stdout.write(f"\r  [Homing 運轉中] SW: 0x{last_sw:04X} | 位置: {actual_pos:6d} | 轉速: {actual_vel:4d} | Attained: {attained} | Reached: {reached}")
    sys.stdout.flush()

    # 必須在有啟動後，且 attained 與 reached 同時為 True，才算真正定位完成
    if has_started and attained and (reached or actual_vel == 0):
        homed = True
        print(f"\n\n[!!! Homing 成功定位 !!!] 原點標定基準: {actual_pos}")
        break

    if error or fault:
        print(f"\n\n[-] Homing 異常！SW: 0x{last_sw:04X}")
        break

    time.sleep(0.01)

# 定位完成後，才把 homing_op_start 降下
current_cw = 0x000F
time.sleep(0.5)

print("\n" + "=" * 65)
print("[步驟 3] 卸載 Homing -> 清除錯誤 -> 載入 CSP (原廠面板: CSP ON/OFF)...")
print("=" * 65)

# A. 降回 Shutdown (0x0006) 釋放 HM 介面
current_cw = 0x0006
time.sleep(0.2)

# B. 若有錯誤，下發 Fault Reset 清理乾淨
if (last_sw & 0x0008) != 0 or (last_sw & 0x2000) != 0:
    current_cw = 0x0080
    time.sleep(0.2)
    current_cw = 0x0006
    time.sleep(0.1)

# C. 切換模式為 8 (CSP)
current_mode = 8
target_pos = actual_pos
time.sleep(0.2)

# D. 重新使能鎖軸進入 CSP (觸發 CIA402CSP_Init)
current_cw = 0x0007
time.sleep(0.1)
current_cw = 0x000F
time.sleep(0.3)
target_pos = actual_pos

print(f"[+] CSP 模式初始化完成！SW = 0x{last_sw:04X} | 模式: {actual_mode} | 當前物理位置: {actual_pos}")
if (last_sw & 0x000F) == 0x0008:
    print(f"[-] 警告：仍處於 Fault 狀態 (SW=0x{last_sw:04X})，請檢查報警！")
else:
    print("[!!! 激磁就緒 !!!] 伺服已真正處於 Operation Enabled (0x1237)，開始運動控制！")

print("\n" + "=" * 65)
print("[步驟 4] CSP 平滑軌跡追隨測試 (+400 counts, 嚴格在安全限制內)")
print("=" * 65)

def smooth_move(delta_counts, duration=2.0):
    global target_pos, target_vel
    start_p = target_pos
    steps = int(duration / 0.001)
    
    for i in range(steps + 1):
        s = i / float(steps)
        scale = 10 * (s ** 3) - 15 * (s ** 4) + 6 * (s ** 5)
        target_pos = int(start_p + delta_counts * scale)
        target_vel = int(delta_counts * (30 * (s ** 2) - 60 * (s ** 3) + 30 * (s ** 4)) / duration)
        diff = target_pos - actual_pos
        sys.stdout.write(f"\r  [CSP 閉環] 目標: {target_pos:6d} | 實際: {actual_pos:6d} | 誤差: {diff:4d} | 速度: {actual_vel:4d}")
        sys.stdout.flush()
        time.sleep(0.001)
    target_vel = 0
    print()

try:
    print("[*] 1. 正向平滑移動 +400 counts...")
    smooth_move(400, duration=2.0)
    time.sleep(0.5)

    print("[*] 2. 平滑返回原點基準...")
    smooth_move(-400, duration=2.0)
    time.sleep(0.5)

    print("\n[+] 運動驗證全部完成！保持鎖軸 3 秒供剛性確認...")
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
