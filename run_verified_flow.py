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

def pack_rxpdo(cw, pos=0, vel=0, mode=6):
    return struct.pack("<Hiib3x", cw, pos, vel, mode)

def unpack_txpdo(data):
    if len(data) >= 14:
        return struct.unpack("<Hiib3x", data[:14])
    return 0, 0, 0, 0

current_cw = 0x0000
target_pos = 0
target_vel = 0
current_mode = 6  # 起始 Mode 6 (Homing)

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

# 躍遷至 OP
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

# 步驟 1: 以 Mode 6 進入 Servo-On
print("\n" + "=" * 65)
print("[步驟 1] 激磁使能 (Mode 6: Homing)...")
print("=" * 65)

for cw in [0x0006, 0x0007, 0x000F]:
    current_cw = cw
    target_pos = actual_pos
    time.sleep(0.2)

print(f"[+] 激磁完成！SW = 0x{last_sw:04X} | 模式: {actual_mode}")

# 步驟 2: 正確的 Homing 閉環握手
print("\n" + "=" * 65)
print("[步驟 2] 觸發 Homing 運轉 (下發 CW = 0x001F)...")
print("=" * 65)

current_cw = 0x001F

# 階段 A: 必須先等待從站將 bit 12 清零 (代表已接收 0x001F 並離開 IDLE 狀態)
print("[*] 等待從站響應 Homing 啟動 (等待 homing_attained -> 0)...")
t_start = time.time()
started = False
while time.time() - t_start < 2.0:
    if (last_sw & 0x1000) == 0:
        started = True
        print(f"[+] 從站已確認啟動 Homing！SW = 0x{last_sw:04X}")
        break
    time.sleep(0.01)

if not started:
    print(f"[*] 提示: 從站未將 bit 12 清零 (可能原點偏移為 0 或已在原點)，繼續監聽完成狀態...")

# 階段 B: 等待真正的 Homing 完成 (bit 12 == 1 且 bit 10 == 1)
t_wait = time.time()
while time.time() - t_wait < 8.0:
    attained = bool(last_sw & 0x1000)
    reached = bool(last_sw & 0x0400)
    error = bool(last_sw & 0x2000)
    sys.stdout.write(f"\r  [Homing 運轉中] SW: 0x{last_sw:04X} | 位置: {actual_pos:6d} | 完成: {attained} | 就位: {reached}")
    sys.stdout.flush()
    if attained and (reached or not started):
        print(f"\n[+] Homing 真正完成！原點位置: {actual_pos}")
        break
    if error:
        print(f"\n[-] Homing 發生錯誤！SW: 0x{last_sw:04X}")
        break
    time.sleep(0.05)

# 下發 0x000F 結束 Homing 運轉信號
current_cw = 0x000F
time.sleep(0.5)

# 步驟 3: 徹底退使能 -> 卸載 HM -> 載入 CSP -> 重新激磁
print("\n" + "=" * 65)
print("[步驟 3] 卸載 Homing 介面 -> 載入 CSP 介面 (FuncInit)...")
print("=" * 65)

# 必須下發 0x0006 讓 bAxisFunctionEnabled=0，觸發 CIA402HM_DeInit()
current_cw = 0x0006
time.sleep(0.2)

# 切換模式為 8 (在 bConfigurationAllowed=1 下生效)
current_mode = 8
target_pos = actual_pos
time.sleep(0.2)
print(f"[*] 當前模式切換確認: 0x6061 = {actual_mode}")

# 重新激磁進入 Operation Enabled，觸發 CIA402CSP_Init() -> MC_SetCommand(MCCMD_SET_CSP_MODE)
current_cw = 0x0007
time.sleep(0.1)
current_cw = 0x000F
time.sleep(0.3)
print(f"[+] CSP 模式完全初始化成功！SW = 0x{last_sw:04X} | 模式: {actual_mode} | 當前位置: {actual_pos}")

# 步驟 4: CSP 平滑微幅運動
print("\n" + "=" * 65)
print("[步驟 4] CSP 運動測試：每秒 300 counts 勻速移動 +600 counts")
print("=" * 65)

try:
    base = actual_pos
    dur = 2.0
    speed = 300
    t_start = time.perf_counter()
    
    while True:
        elapsed = time.perf_counter() - t_start
        if elapsed >= dur:
            target_pos = base + 600
            target_vel = 0
            break
        target_pos = int(base + elapsed * speed)
        target_vel = speed
        diff = target_pos - actual_pos
        sys.stdout.write(f"\r  [CSP 運動] 目標: {target_pos:6d} | 實際: {actual_pos:6d} | 誤差: {diff:5d} | 反饋速度: {actual_vel:4d}")
        sys.stdout.flush()
        time.sleep(0.002)
    print()
    print("[+] 移動結束，保持鎖定 2 秒...")
    time.sleep(2.0)

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
