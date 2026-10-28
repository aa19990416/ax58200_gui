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

# 自動映射 PDO (14 bytes in / 14 bytes out)
master.config_map()

def pack_rxpdo(cw, pos=0, vel=0, mode=9):
    return struct.pack("<Hiib3x", cw, pos, vel, mode)

def unpack_txpdo(data):
    if len(data) >= 14:
        return struct.unpack("<Hiib3x", data[:14])
    return 0, 0, 0, 0

current_cw = 0x0000
target_pos = 0
target_vel = 0
current_mode = 9

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

# -------------------------------------------------------------
# 標準 ESM 狀態切換：PREOP -> SAFEOP -> OP
# -------------------------------------------------------------
print("[*] 正在透過標準流程請求 OP 狀態...")
slave.state = pysoem.SAFEOP_STATE
master.write_state()
master.state_check(pysoem.SAFEOP_STATE, 50000)
time.sleep(0.1)

slave.state = pysoem.OP_STATE
master.write_state()
master.state_check(pysoem.OP_STATE, 50000)
time.sleep(0.2)

# 若標準躍遷超時，透過常規手段輔助
al_stat = struct.unpack("<H", slave._fprd(0x0130, 2))[0]
if (al_stat & 0x000F) != 0x0008:
    print(f"[*] AL Status (0x0130) = 0x{al_stat:04X}，發送 OP 請求...")
    slave._fpwr(0x0120, struct.pack("<H", 0x0008))
    time.sleep(0.2)
    al_stat = struct.unpack("<H", slave._fprd(0x0130, 2))[0]

print(f"[+] 從站 AL Status: 0x{al_stat:04X} | 週期資料 WKC: {last_wkc}")

# 等待接收第一筆有效 PDO
t0 = time.time()
while time.time() - t0 < 3.0:
    if last_wkc > 0 and last_sw != 0:
        break
    time.sleep(0.05)

print(f"[+] PDO 通訊確認: WKC={last_wkc} | 當前 SW = 0x{last_sw:04X} | 反饋模式: {actual_mode}")

if last_wkc == 0 or last_sw == 0:
    print("[-] 錯誤：仍未收到有效 Process Data！請檢查網路線連接。")
    running = False
    th.join()
    master.close()
    sys.exit(1)

# =============================================================
# 步驟 0: 自動軟體 Fault Reset (若有報警則排除)
# =============================================================
if (last_sw & 0x0008) != 0:
    print(f"[*] 檢測到 Fault (SW=0x{last_sw:04X})，下發 0x0000 -> 0x0080 正緣復位...")
    current_cw = 0x0000
    time.sleep(0.3)
    current_cw = 0x0080
    time.sleep(0.4)
    current_cw = 0x0000
    time.sleep(0.2)
    print(f"[*] 復位後 SW = 0x{last_sw:04X}")

# =============================================================
# 步驟 1: 依序激磁使能 (0x0006 -> 0x0007 -> 0x000F)
# =============================================================
print("\n" + "=" * 65)
print("[步驟 1] 依序下發 Servo-ON (觀察軸心是否建立電磁吸合鎖軸)...")
print("=" * 65)

current_cw = 0x0006
time.sleep(0.2)
print(f"[*] Ready to Switch On: SW = 0x{last_sw:04X}")

current_cw = 0x0007
time.sleep(0.1)
print(f"[*] Switched On        : SW = 0x{last_sw:04X}")

current_cw = 0x000F
print("[*] 下發 Operation Enabled (0x000F)，等待 1.5 秒讓 FOC 充磁吸合...")
time.sleep(1.5)

print(f"[+] 當前狀態: SW = 0x{last_sw:04X} | 模式: {actual_mode} | 當前位置: {actual_pos}")

if (last_sw & 0x006F) == 0x0027 or (last_sw & 0x000F) == 0x0007:
    print("\n[!!! 激磁成功 !!!] 伺服已真正使能！馬達軸現在應該處於堅硬的鎖定狀態（請用手試轉確認）！")
else:
    print(f"\n[-] 使能未達成 (SW=0x{last_sw:04X})，請檢查是否報警。")

print("\n保持激磁鎖軸 5 秒供確認...")
time.sleep(5.0)

# 結束釋放
current_cw = 0x0000
time.sleep(0.2)
running = False
th.join()
slave.output = bytes(len(slave.output))
master.send_processdata()
master.close()
print("[*] 伺服斷電釋放完成。")
