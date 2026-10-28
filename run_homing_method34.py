import pysoem
import struct
import time
import threading
import sys

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到 EtherCAT 從站！")
    sys.exit(1)

slave = master.slaves[0]
print(f"[+] 找到從站: {slave.name}")

def read_u16(addr):
    try:
        return struct.unpack("<H", slave._fprd(addr, 2))[0]
    except Exception:
        return None

def write_u16(addr, val):
    slave._fpwr(addr, struct.pack("<H", val))

# 0. 軟體清除 AL Error (免手動按鍵)
cur_al = read_u16(0x0130)
if cur_al and (cur_al & 0x0010):
    print(f"[*] 偵測到 AL Error 標記 (0x{cur_al:04X})，下發 Error Acknowledge...")
    write_u16(0x0120, (cur_al & 0x000F) | 0x0010)
    time.sleep(0.1)
    write_u16(0x0120, 0x0002)
    time.sleep(0.2)

# 在 PREOP 寫入正確的 Homing Method 34 與安全參數
slave.state = pysoem.PREOP_STATE
master.write_state()
master.state_check(pysoem.PREOP_STATE, 50000)
time.sleep(0.1)

# 1. 核心修正：將 Homing Method 改為 34 (正向搜尋 Z 相，符合 mc.c 軌跡方向)
try:
    slave.sdo_write(0x6098, 0, struct.pack("<b", 34))
    m_now = struct.unpack("<b", slave.sdo_read(0x6098, 0))[0]
    print(f"[+] 0x6098 Homing Method 已確立為: {m_now} (正向尋找 Z 相)")
except Exception as e:
    print(f"[-] 寫入 0x6098 失敗: {e}")

# 2. 放寬跟隨誤差與設定平穩搜尋速度
try:
    slave.sdo_write(0x3130, 0, struct.pack("<i", 20000))
    slave.sdo_write(0x6099, 1, struct.pack("<I", 300))
    slave.sdo_write(0x6099, 2, struct.pack("<I", 300))
    slave.sdo_write(0x607C, 0, struct.pack("<i", 0))
    print("[+] 0x6099 速度與誤差參數已同步就緒")
except Exception as e:
    print(f"[*] 輔助參數提示: {e}")

# 配置 PDO
master.config_map()

# 打包 RxPDO (全程固定 Mode 6: Homing)
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

        delay = 0.001 - (time.perf_counter() - t0)
        if delay > 0:
            time.sleep(delay)

th = threading.Thread(target=pdo_loop, daemon=True)
th.start()

# 躍遷進入 OP (穩定 Free-Run 模式)
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

al_stat = read_u16(0x0130)
print(f"[+] OP 狀態確認: 0x{al_stat:04X} | 週期資料 WKC: {last_wkc}")

t0 = time.time()
while time.time() - t0 < 3.0:
    if last_wkc > 0 and last_sw != 0:
        break
    time.sleep(0.05)

print(f"[+] 通訊就緒！SW = 0x{last_sw:04X} | 模式: {actual_mode} | 當前位置: {actual_pos}")

# 軟體清除 CiA 402 殘留報警
if (last_sw & 0x0008) != 0 or (last_sw & 0x2000) != 0:
    print(f"[*] 檢測到 Fault (SW=0x{last_sw:04X})，下發正緣復位序列...")
    current_cw = 0x0000
    time.sleep(0.2)
    current_cw = 0x0080
    time.sleep(0.3)
    current_cw = 0x0000
    time.sleep(0.2)
    print(f"[*] 復位後 SW = 0x{last_sw:04X}")

# =============================================================
# 步驟 1: 原廠 Servo-ON 使能與電角度充磁吸合
# =============================================================
print("\n" + "=" * 65)
print("[步驟 1] 原廠 Servo-ON 使能與電角度充磁吸合...")
print("=" * 65)

current_cw = 0x0006
time.sleep(0.2)

current_cw = 0x0007
time.sleep(0.15)

current_cw = 0x000F
print("[*] 下發 0x000F，通電充磁吸合中，靜置 1.5 秒...")
time.sleep(1.5)

print(f"[+] 充磁鎖軸完成！SW = 0x{last_sw:04X} | 模式: {actual_mode} | 吸合穩定位置: {actual_pos}")

if (last_sw & 0x000F) != 0x0007 and (last_sw & 0x006F) != 0x0027:
    print(f"[-] 使能狀態異常 (SW=0x{last_sw:04X})，終止。")
    current_cw = 0x0000
    running = False
    th.join()
    sys.exit(1)

# =============================================================
# 步驟 2: 原廠 Homing 觸發 (正向旋轉尋找 Z 相索引脈衝)
# =============================================================
print("\n" + "=" * 65)
print("[步驟 2] 點擊 Homing (下發 CW = 0x001F，開始旋轉尋找原點)...")
print("=" * 65)

current_cw = 0x001F

t_start = time.time()
homed = False
has_started = False

while time.time() - t_start < 12.0:
    attained = bool(last_sw & 0x1000)
    reached  = bool(last_sw & 0x0400)
    error    = bool(last_sw & 0x2000)
    fault    = bool(last_sw & 0x0008)

    if (last_sw & 0x1000) == 0:
        has_started = True

    sys.stdout.write(f"\r  [Homing 運轉中] SW: 0x{last_sw:04X} | 實際位置: {actual_pos:6d} | 轉速: {actual_vel:4d} | Attained: {attained}")
    sys.stdout.flush()

    # 捕獲 Z 相脈衝且原點確立
    if has_started and attained and (reached or actual_vel == 0):
        homed = True
        print(f"\n\n[!!! Homing 完美完成 !!!] 編碼器 Z 相索引脈衝已捕獲！標定原點位置: {actual_pos}")
        break

    if error or fault:
        print(f"\n\n[-] Homing 異常！SW: 0x{last_sw:04X}")
        break

    time.sleep(0.01)

current_cw = 0x000F
time.sleep(0.5)

if homed:
    print(f"[+] 原點定位成功！軸心目前保持在標定零位 (位置: {actual_pos})。")
    print("[*] 保持原點鎖定 3 秒供剛性確認...")
    time.sleep(3.0)
else:
    print("\n[-] Homing 未完成，請觀察數值。")

# 規範化降使能退出
print("\n[*] 規範化降使能退出...")
current_cw = 0x0007
time.sleep(0.1)
current_cw = 0x0006
time.sleep(0.1)
current_cw = 0x0000
time.sleep(0.2)

running = False
th.join()
slave.output = bytes(len(slave.output))
master.send_processdata()
master.close()
print("[+] 伺服已安全斷電釋放，狀態機完全乾淨。")
