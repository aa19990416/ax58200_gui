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

# 在 PREOP 先明確鎖定 Mode 8 (CSP)
slave.state = pysoem.PREOP_STATE
master.write_state()
master.state_check(pysoem.PREOP_STATE, 50000)
time.sleep(0.1)

try:
    slave.sdo_write(0x6060, 0, struct.pack("<b", 8))
except Exception:
    pass

master.config_map()

def pack_rxpdo(cw, pos=0, vel=0, mode=8):
    return struct.pack("<Hiib3x", cw, pos, vel, mode)

def unpack_txpdo(data):
    if len(data) >= 14:
        return struct.unpack("<Hiib3x", data[:14])
    return 0, 0, 0, 0

current_cw = 0x0000
target_pos = 0
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
        slave.output = pack_rxpdo(current_cw, target_pos, 0, 8)
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

# 躍遷至 OP
slave._fpwr(0x0120, struct.pack("<H", 0x0004))
time.sleep(0.1)
slave._fpwr(0x0120, struct.pack("<H", 0x0008))
time.sleep(0.2)

# 對齊起始反饋位置
time.sleep(0.1)
target_pos = actual_pos

print("\n[*] 正在執行 CiA 402 使能序列...")
for cw in [0x0006, 0x0007, 0x000F]:
    current_cw = cw
    target_pos = actual_pos
    time.sleep(0.3)

target_pos = actual_pos
print(f"[+] 伺服激磁完成！SW = 0x{last_sw:04X} | 當前位置 = {actual_pos}")
print("=" * 70)
print("[*] 伺服現已進入【永久常駐上電鎖軸模式】！")
print("    - 請試著用手轉動馬達軸心，感受是否有強大的磁吸鎖定阻力。")
print("    - 若需安全斷電退出，請隨時按下 [Ctrl + C]。")
print("=" * 70)

try:
    while True:
        diff = target_pos - actual_pos
        is_op_enabled = (last_sw & 0x006F) == 0x0027
        status_str = "OPERATION_ENABLED (鎖軸中)" if is_op_enabled else f"異常斷開(0x{last_sw:04X})"
        
        sys.stdout.write(
            f"\r[常駐上電] 狀態: {status_str} | 目標: {target_pos:6d} | 實際: {actual_pos:6d} | 偏差: {diff:4d} | 速度: {actual_vel:4d} | WKC: {last_wkc}"
        )
        sys.stdout.flush()
        time.sleep(0.08)

except KeyboardInterrupt:
    print("\n\n[*] 收到中斷信號，正在安全釋放伺服...")

finally:
    current_cw = 0x0000
    time.sleep(0.2)
    running = False
    th.join()
    slave.output = bytes(len(slave.output))
    master.send_processdata()
    master.close()
    print("[*] 伺服已安全斷電釋放。")
