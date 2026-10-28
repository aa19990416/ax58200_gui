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

# 純淨配置：不寫多餘 SDO
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

def auto_fault_reset():
    global current_cw
    print("[*] 正在透過軟體下發 Fault Reset (0x0080 -> 0x0000)...")
    current_cw = 0x0080
    time.sleep(0.3)
    current_cw = 0x0000
    time.sleep(0.15)

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

# =====================================================================
# 步驟 0: 開機自動清除報警（免手動按板端 Reset 鈕）
# =====================================================================
if (last_sw & 0x0008) != 0 or (last_sw & 0x2000) != 0:
    print(f"[*] 檢測到殘留報警 (SW=0x{last_sw:04X})，執行自動復位...")
    auto_fault_reset()

# =====================================================================
# 步驟 1: Servo-On 充磁吸合（電機角與機械角初始對齊）
# =====================================================================
print("\n" + "=" * 65)
print("[步驟 1] Servo-On 充磁吸合對齊 (pwmALI)...")
print("=" * 65)

current_cw = 0x0006
target_pos = actual_pos
time.sleep(0.2)

current_cw = 0x0007
time.sleep(0.1)

# 送 0x000F 觸發 MC_StartMotor() 進行向量對齊充磁
current_cw = 0x000F
target_pos = actual_pos
print("[*] 通電充磁中，等待 1.0 秒完成轉子初始對齊...")
time.sleep(1.0)

target_pos = actual_pos
print(f"[+] 充磁對齊完成！SW = 0x{last_sw:04X} | 模式: {actual_mode} | 吸合穩定位置: {actual_pos}")

# =====================================================================
# 步驟 2: 接續執行 Homing (旋轉掃描 Z 相以標定原點)
# =====================================================================
print("\n" + "=" * 65)
print("[步驟 2] 觸發 Homing (下發 CW = 0x001F，旋轉尋找 Z 相)...")
print("=" * 65)

current_cw = 0x001F

t_start = time.time()
homed = False
while time.time() - t_start < 8.0:
    attained = bool(last_sw & 0x1000)
    reached  = bool(last_sw & 0x0400)
    error    = bool(last_sw & 0x2000)
    fault    = bool(last_sw & 0x0008)

    sys.stdout.write(f"\r  [Homing 運轉中] SW: 0x{last_sw:04X} | 位置: {actual_pos:6d} | 轉速: {actual_vel:4d} | Attained: {attained}")
    sys.stdout.flush()

    if attained and (reached or actual_vel == 0):
        homed = True
        print(f"\n[+] Homing 標定完成！原點位置: {actual_pos}")
        break

    if error or fault:
        print(f"\n[-] Homing 異常！SW: 0x{last_sw:04X}")
        break
    time.sleep(0.02)

current_cw = 0x000F
time.sleep(0.3)

# =====================================================================
# 步驟 3: 乾淨切換至 CSP 模式 (Mode 8)
# =====================================================================
print("\n" + "=" * 65)
print("[步驟 3] 卸載 Homing -> 啟動 CSP 模式...")
print("=" * 65)

current_cw = 0x0006
time.sleep(0.2)

if (last_sw & 0x0008) != 0:
    auto_fault_reset()
    current_cw = 0x0006
    time.sleep(0.1)

current_mode = 8
target_pos = actual_pos
time.sleep(0.2)

current_cw = 0x0007
time.sleep(0.1)
current_cw = 0x000F
time.sleep(0.3)
target_pos = actual_pos

print(f"[+] CSP 模式完全就緒！SW = 0x{last_sw:04X} | 模式: {actual_mode} | 當前物理基準: {actual_pos}")

# =====================================================================
# 步驟 4: 正常正向平滑軌跡閉環運動 (posMXE = 500 安全範圍內)
# =====================================================================
print("\n" + "=" * 65)
print("[步驟 4] 正常正向與反向平滑往返運動 (±300 counts)...")
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
        sys.stdout.write(f"\r  [平滑CSP] 目標: {target_pos:6d} | 實際: {actual_pos:6d} | 誤差: {diff:4d} | 速度: {actual_vel:4d}")
        sys.stdout.flush()
        time.sleep(0.001)
    target_vel = 0
    print()

try:
    print("[*] 1. 正常正向旋轉 (+300 counts)...")
    smooth_move(300, duration=2.0)
    time.sleep(0.5)

    print("[*] 2. 正常返回原位 (-300 counts)...")
    smooth_move(-300, duration=2.0)
    time.sleep(0.5)

    print("\n[+] 運動驗證完成！保持鎖軸 3 秒確認剛性...")
    time.sleep(3.0)

except KeyboardInterrupt:
    print("\n[*] 使用者中斷。")

finally:
    print("[*] 退出前自動復位並斷電釋放...")
    current_cw = 0x0080
    time.sleep(0.1)
    current_cw = 0x0000
    time.sleep(0.1)
    running = False
    th.join()
    slave.output = bytes(len(slave.output))
    master.send_processdata()
    master.close()
    print("[+] 伺服已安全斷電釋放，狀態已自動復位。")
