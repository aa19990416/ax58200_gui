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

# 1. 軟體消除前次殘留之 AL Error (0x0012 等)
cur_al = read_u16(0x0130)
if cur_al and (cur_al & 0x0010):
    print(f"[*] 檢測到 AL Error 標記 (0x{cur_al:04X})，執行清除確認...")
    write_u16(0x0120, (cur_al & 0x000F) | 0x0010)
    time.sleep(0.1)
    write_u16(0x0120, 0x0002) # 退回 PREOP
    time.sleep(0.2)

# 2. 自動配置 PDO
master.config_map()

# 打包 RxPDO (14 Bytes: CW, TargetPos, TargetVel, Mode, 3-byte pad)
def pack_rxpdo(cw, pos=0, vel=0, mode=9):
    return struct.pack("<Hiib3x", cw, pos, vel, mode)

def unpack_txpdo(data):
    if len(data) >= 14:
        return struct.unpack("<Hiib3x", data[:14])
    return 0, 0, 0, 0

current_cw = 0x0000
target_pos = 0
target_vel = 0
current_mode = 9  # 起始就鎖定 Mode 9

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

# 3. 不開 DC 同步，以 Free-Run 方式躍遷進入 OP
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

if al_stat != 0x0008:
    print("[-] 警告：仍未進入 OP 狀態！")

# 等待通訊穩定
t0 = time.time()
while time.time() - t0 < 3.0:
    if last_wkc > 0 and last_sw != 0:
        break
    time.sleep(0.05)

print(f"[+] 通訊就緒！SW = 0x{last_sw:04X} | 反饋模式: {actual_mode} | 起始位置: {actual_pos}")

# =============================================================
# 步驟 0: 正緣 Fault Reset (若有 0x08 報警則排除)
# =============================================================
if (last_sw & 0x0008) != 0:
    print(f"[*] 檢測到 Fault (SW=0x{last_sw:04X})，下發 0x0080 正緣復位...")
    current_cw = 0x0000
    time.sleep(0.2)
    current_cw = 0x0080
    time.sleep(0.3)
    current_cw = 0x0000
    time.sleep(0.2)
    print(f"[*] 復位後 SW = 0x{last_sw:04X}")

# =============================================================
# 步驟 1: 先在待命態確立 Mode 9，再順序使能
# =============================================================
print("\n" + "=" * 65)
print("[步驟 1] 先行載入 Mode 9，再執行 Servo-ON...")
print("=" * 65)

# A. 待命態 (0x0006)，下發 Mode 9
current_cw = 0x0006
current_mode = 9
time.sleep(0.3)
print(f"[*] Ready to Switch On: SW = 0x{last_sw:04X} | 模式: {actual_mode}")

# B. 準備態 (0x0007)
current_cw = 0x0007
time.sleep(0.15)
print(f"[*] Switched On        : SW = 0x{last_sw:04X} | 模式: {actual_mode}")

# C. 激磁使能態 (0x000F) -> 觸發充磁吸合與 CSV_Init
current_cw = 0x000F
print("[*] 下發 0x000F，充磁吸合中，靜置 1.2 秒...")
time.sleep(1.2)

print(f"[+] 使能完成！SW = 0x{last_sw:04X} | 模式: {actual_mode} | 穩定位置: {actual_pos}")

if (last_sw & 0x000F) == 0x0007:
    print("\n[!!! 激磁成功 !!!] 伺服已真正使能鎖軸！現在軸心應具備強烈鎖定剛性！")
else:
    print(f"\n[-] 警告：使能狀態異常 (SW=0x{last_sw:04X})")

# =============================================================
# 步驟 2: CSV 速度運轉 (由 0 加速至 800)
# =============================================================
print("\n" + "=" * 65)
print("[步驟 2] CSV 速度運轉 (0 -> 800 rpm)...")
print("=" * 65)

try:
    print("[*] 1. 正向平滑加速 (0 -> 800)...")
    for v in range(0, 801, 20):
        target_vel = v
        sys.stdout.write(f"\r  [CSV 正轉] 指令速度: {target_vel:4d} | 反饋轉速: {actual_vel:4d} | 反饋位置: {actual_pos:7d}")
        sys.stdout.flush()
        time.sleep(0.04)
    print()

    print("[*] 維持指令 800 運轉 3 秒...")
    t_end = time.time() + 3.0
    while time.time() < t_end:
        sys.stdout.write(f"\r  [CSV 恆速] 指令速度: {target_vel:4d} | 反饋轉速: {actual_vel:4d} | 反饋位置: {actual_pos:7d}")
        sys.stdout.flush()
        time.sleep(0.05)
    print()

    print("[*] 2. 減速停機...")
    for v in range(800, -1, -20):
        target_vel = v
        sys.stdout.write(f"\r  [CSV 減速] 指令速度: {target_vel:4d} | 反饋轉速: {actual_vel:4d} | 反饋位置: {actual_pos:7d}")
        sys.stdout.flush()
        time.sleep(0.02)
    print()

    print("\n[+] 運轉測試完畢！")

except KeyboardInterrupt:
    print("\n[*] 使用者中斷。")

finally:
    target_vel = 0
    current_cw = 0x0000
    time.sleep(0.1)
    current_cw = 0x0080
    time.sleep(0.1)
    current_cw = 0x0000
    time.sleep(0.1)
    running = False
    th.join()
    slave.output = bytes(len(slave.output))
    master.send_processdata()
    master.close()
    print("[+] 伺服斷電釋放完成。")
