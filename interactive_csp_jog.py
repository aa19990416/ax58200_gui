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

# 在 PREOP 預先設定 Mode 8 (CSP)
slave.state = pysoem.PREOP_STATE
master.write_state()
master.state_check(pysoem.PREOP_STATE, 50000)
time.sleep(0.1)

try:
    slave.sdo_write(0x6060, 0, struct.pack("<b", 8))
except Exception:
    pass

master.config_map()

# 打包 RxPDO (14 Bytes: CW, TargetPos, TargetVel, Mode, 3-byte pad)
def pack_rxpdo(cw, pos=0, vel=0, mode=8):
    return struct.pack("<Hiib3x", cw, pos, vel, mode)

# 解包 TxPDO (14 Bytes: SW, ActPos, ActVel, ActMode, 3-byte pad)
def unpack_txpdo(data):
    if len(data) >= 14:
        return struct.unpack("<Hiib3x", data[:14])
    return 0, 0, 0, 0

current_cw = 0x0000
target_pos = 0
target_vel = 0
current_mode = 8

last_sw = 0
actual_pos = 0
actual_vel = 0
actual_mode = 0
running = True

# 1ms 週期性 PDO 收發迴圈
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

# 平滑使能流程
print("[*] 正在執行平滑無衝擊 Servo-On...")
for cw, expect in [(0x0006, 0x0021), (0x0007, 0x0023), (0x000F, 0x0027)]:
    current_cw = cw
    target_pos = actual_pos
    t_wait = time.time() + 1.5
    while time.time() < t_wait:
        if (last_sw & 0x006F) == expect:
            break
        time.sleep(0.02)

if (last_sw & 0x006F) != 0x0027:
    print(f"[-] 使能失敗，SW: 0x{last_sw:04X}")
    running = False
    th.join()
    exit()

print(f"[!!! 使能成功 !!!] 馬達已平滑激磁鎖軸 (SW=0x{last_sw:04X})，基準位置: {actual_pos}\n")

# 平滑軌跡移動產生器 (5次多項式內插)
def move_to(target, duration):
    global target_pos
    start = target_pos
    steps = int(duration / 0.005)
    for i in range(steps + 1):
        s = i / steps
        # 5次多項式 s 曲線: 10*s^3 - 15*s^4 + 6*s^5
        scale = 10 * (s ** 3) - 15 * (s ** 4) + 6 * (s ** 5)
        target_pos = int(start + (target - start) * scale)
        diff = target_pos - actual_pos
        sys.stdout.write(f"\r  [運動中] 目標: {target_pos:7d} | 實際: {actual_pos:7d} | 誤差: {diff:4d} | 速度: {actual_vel:5d}")
        sys.stdout.flush()
        time.sleep(0.005)
    print()

try:
    while True:
        print("\n" + "=" * 65)
        print("CSP 控制測試選單：")
        print("  1: 微幅正反點動測試 (±1000 counts, 約 0.1 圈)")
        print("  2: 單圈旋轉測試 (正轉一圈 10000 counts 後返回)")
        print("  3: 正弦連續往復運動 (Sinusoidal Tracking)")
        print("  Q: 安全退使能並退出")
        print("=" * 65)
        choice = input("請輸入選項 [1/2/3/Q]: ").strip().upper()

        if choice == "1":
            base = actual_pos
            print("[*] 正轉 +1000 counts...")
            move_to(base + 1000, 1.0)
            time.sleep(0.5)
            print("[*] 返回原位...")
            move_to(base, 1.0)
            print("[+] 點動測試完成。")

        elif choice == "2":
            base = actual_pos
            print("[*] 正轉 1 圈 (+10000 counts)...")
            move_to(base + 10000, 2.5)
            time.sleep(0.5)
            print("[*] 反轉回原位...")
            move_to(base, 2.5)
            print("[+] 單圈旋轉完成。")

        elif choice == "3":
            base = actual_pos
            amp = 5000
            period = 4.0
            cycles = 2
            print(f"[*] 執行正弦波運動 (振幅: ±{amp} counts, 週期: {period}s, 共 {cycles} 週期)...")
            t_start = time.time()
            total_dur = period * cycles
            while time.time() - t_start < total_dur:
                t_cur = time.time() - t_start
                target_pos = int(base + amp * math.sin(2.0 * math.pi * t_cur / period))
                diff = target_pos - actual_pos
                sys.stdout.write(f"\r  [正弦追隨] 目標: {target_pos:7d} | 實際: {actual_pos:7d} | 誤差: {diff:4d} | 速度: {actual_vel:5d}")
                sys.stdout.flush()
                time.sleep(0.005)
            print("\n[*] 平滑減速歸位...")
            move_to(base, 1.0)
            print("[+] 正弦運動完成。")

        elif choice == "Q":
            break

except KeyboardInterrupt:
    print("\n[*] 使用者中斷測試。")

finally:
    print("\n[*] 正在退使能並關閉 EtherCAT 連線...")
    current_cw = 0x0000
    time.sleep(0.2)
    running = False
    th.join()
    slave.output = bytes(len(slave.output))
    master.send_processdata()
    master.close()
    print("[*] 伺服已安全斷電釋放。")
