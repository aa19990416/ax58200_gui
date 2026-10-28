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

# 0. 軟體消除 AL Error (免手動按鍵)
cur_al = read_u16(0x0130)
if cur_al and (cur_al & 0x0010):
    print(f"[*] 偵測到 AL Error 標記 (0x{cur_al:04X})，下發 Error Acknowledge...")
    write_u16(0x0120, (cur_al & 0x000F) | 0x0010)
    time.sleep(0.1)
    write_u16(0x0120, 0x0002)
    time.sleep(0.2)

# 配置 PDO
master.config_map()

# 打包 RxPDO (保持在 Mode 8: CSP 或 Mode 6，只發 0x000F 保持鎖定)
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

# 躍遷進入 OP (穩定 Free-Run)
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

print(f"[+] 通訊就緒！SW = 0x{last_sw:04X} | 模式: {actual_mode} | 當前物理位置: {actual_pos}")

# 軟體清除 CiA 402 殘留報警
if (last_sw & 0x0008) != 0:
    print(f"[*] 檢測到 Fault (SW=0x{last_sw:04X})，下發正緣復位序列...")
    current_cw = 0x0000
    time.sleep(0.2)
    current_cw = 0x0080
    time.sleep(0.3)
    current_cw = 0x0000
    time.sleep(0.2)
    print(f"[*] 復位後 SW = 0x{last_sw:04X}")

# =============================================================
# 依序激磁使能 (0x0006 -> 0x0007 -> 0x000F)
# =============================================================
print("\n" + "=" * 65)
print("[步驟 1] 依序下發 Servo-On 使能與充磁吸合...")
print("=" * 65)

current_cw = 0x0006
time.sleep(0.2)
print(f"[*] Ready to Switch On (0x0006): SW = 0x{last_sw:04X}")

current_cw = 0x0007
time.sleep(0.15)
print(f"[*] Switched On        (0x0007): SW = 0x{last_sw:04X}")

current_cw = 0x000F
print("[*] 下發 Operation Enabled (0x000F)，通電充磁吸合中，靜置 1.5 秒...")
time.sleep(1.5)

# 將吸合完成後的實際位置固定為目標位置，閉環定格鎖死
target_pos = actual_pos

print(f"[+] 充磁鎖軸完成！SW = 0x{last_sw:04X} | 模式: {actual_mode} | 吸合固定位置: {actual_pos}")

if (last_sw & 0x000F) == 0x0007 or (last_sw & 0x006F) == 0x0027:
    print("\n[✔ 鎖軸成功] 馬達已進入剛性閉環鎖定！")
    print("[*] 正在持續維持充磁吸合狀態... (請用手試轉軸心感受阻力，按 Ctrl+C 可停止釋放)")
else:
    print(f"[-] 警告：使能狀態異常 (SW=0x{last_sw:04X})")

# =============================================================
# 持續維持鎖軸 (無窮迴圈，直到使用者按 Ctrl+C)
# =============================================================
try:
    sec = 0
    while True:
        sys.stdout.write(f"\r  [持續鎖軸中] 已維持: {sec:4d} 秒 | SW: 0x{last_sw:04X} | 鎖定位置: {actual_pos:6d} | 轉速: {actual_vel:2d}")
        sys.stdout.flush()
        time.sleep(1.0)
        sec += 1

except KeyboardInterrupt:
    print("\n\n[*] 收到使用者中止信號 (Ctrl+C)，開始執行安全斷電...")

finally:
    # 規範化降使能退出，確保不留任何報警
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
    print("[+] 伺服已安全斷電釋放，馬達軸已解除鎖定。")
