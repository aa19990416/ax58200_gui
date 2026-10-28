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

def pack_rxpdo(cw, pos=0, vel=0, mode=8):
    return struct.pack("<Hiib3x", cw, pos, vel, mode)

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
print(f"[+] OP 狀態確立: 0x{read_u16(0x0130):04X} | 起始位置: {actual_pos}")

# 使能流程
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

print(f"[+] 使能完成！SW = 0x{last_sw:04X} | 模式: {actual_mode} | 當前基準: {actual_pos}")

print("\n" + "=" * 65)
print("極性反相鏡像測試：以鏡像指令驅動馬達平滑往返 ±300 counts")
print("=" * 65)

# 鏡像運動控制函式
def move_mirrored(desired_physical_delta, duration=2.0):
    global target_pos
    start_logical_pos = target_pos
    start_actual_pos = actual_pos
    steps = int(duration / 0.001)
    
    # 物理正向對應指令負向
    command_delta = -desired_physical_delta

    for i in range(steps + 1):
        s = i / float(steps)
        scale = 10 * (s ** 3) - 15 * (s ** 4) + 6 * (s ** 5)
        target_pos = int(start_logical_pos + command_delta * scale)
        
        expected_actual = int(start_actual_pos + desired_physical_delta * scale)
        tracking_err = expected_actual - actual_pos
        sys.stdout.write(f"\r  [鏡像CSP] 預期物理位置: {expected_actual:6d} | 實際反饋: {actual_pos:6d} | 追隨誤差: {tracking_err:4d} | 轉速: {actual_vel:4d}")
        sys.stdout.flush()
        time.sleep(0.001)
    print()

try:
    print("[*] 1. 物理正轉 +300 counts...")
    move_mirrored(300, duration=2.0)
    time.sleep(0.5)

    print("[*] 2. 物理返回原位 (-300 counts)...")
    move_mirrored(-300, duration=2.0)
    time.sleep(0.5)

    print("[*] 3. 物理反轉 -300 counts...")
    move_mirrored(-300, duration=2.0)
    time.sleep(0.5)

    print("[*] 4. 物理返回原位 (+300 counts)...")
    move_mirrored(300, duration=2.0)
    time.sleep(0.5)

    print("\n[+] 鏡像正反向閉環測試全部完成！")

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
