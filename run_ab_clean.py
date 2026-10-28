import pysoem
import struct
import time
import sys

print("[*] 正在開啟 eth0...", flush=True)
master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到 EtherCAT 從站！", flush=True)
    sys.exit(1)

slave = master.slaves[0]
print(f"[+] 找到從站: {slave.name}", flush=True)

# 0. 軟體清除 AL Error (0x0120)
try:
    cur_al = struct.unpack("<H", slave._fprd(0x0130, 2))[0]
    if cur_al & 0x0010:
        print(f"[*] 清除 AL Error (0x{cur_al:04X})...", flush=True)
        slave._fpwr(0x0120, struct.pack("<H", (cur_al & 0x000F) | 0x0010))
        time.sleep(0.1)
        slave._fpwr(0x0120, struct.pack("<H", 0x0002))
        time.sleep(0.2)
except Exception as e:
    print(f"[*] AL 清除提示: {e}", flush=True)

# 1. 在 PREOP 復原原廠預設速度
slave.state = pysoem.PREOP_STATE
master.write_state()
master.state_check(pysoem.PREOP_STATE, 50000)
time.sleep(0.1)

try:
    slave.sdo_write(0x6099, 1, struct.pack("<I", 0))
    slave.sdo_write(0x6099, 2, struct.pack("<I", 10))
    slave.sdo_write(0x607C, 0, struct.pack("<i", 0))
    s1 = struct.unpack("<I", slave.sdo_read(0x6099, 1))[0]
    s2 = struct.unpack("<I", slave.sdo_read(0x6099, 2))[0]
    m0 = struct.unpack("<b", slave.sdo_read(0x6098, 0))[0]
    print(f"[+] SDO 參數復原完成: Method={m0} | 6099:01={s1} | 6099:02={s2}", flush=True)
except Exception as e:
    print(f"[-] SDO 寫入失敗: {e}", flush=True)

# 2. 配置 PDO
master.config_map()

slave.state = pysoem.SAFEOP_STATE
master.write_state()
master.state_check(pysoem.SAFEOP_STATE, 50000)
time.sleep(0.1)

# 送往 OP
slave._fpwr(0x0120, struct.pack("<H", 0x0008))
time.sleep(0.2)
al_stat = struct.unpack("<H", slave._fprd(0x0130, 2))[0]
print(f"[+] 從站 AL Status: 0x{al_stat:04X}", flush=True)

# PDO 打包與解包函式
def send_cycle(cw, mode=6):
    slave.output = struct.pack("<Hiib3x", cw, 0, 0, mode)
    master.send_processdata()
    wkc = master.receive_processdata(2000)
    sw, pos, vel, m = 0, 0, 0, 0
    if wkc > 0 and slave.input and len(slave.input) >= 14:
        sw, pos, vel, m = struct.unpack("<Hiib3x", slave.input[:14])
    return wkc, sw, pos, vel, m

# 建立通信
for _ in range(50):
    wkc, sw, pos, vel, m = send_cycle(0x0000)
    time.sleep(0.002)

print(f"[+] 通訊就緒！WKC={wkc} | 當前 SW=0x{sw:04X} | 模式={m} | 位置={pos}", flush=True)

# 軟體清除 CiA 402 Fault (0x0080 正緣復位)
if (sw & 0x0008) != 0 or (sw & 0x2000) != 0:
    print(f"[*] 檢測到 Fault (SW=0x{sw:04X})，下發 0x0080 正緣復位...", flush=True)
    for _ in range(100):
        send_cycle(0x0000)
        time.sleep(0.002)
    for _ in range(150):
        send_cycle(0x0080)
        time.sleep(0.002)
    for _ in range(100):
        send_cycle(0x0000)
        time.sleep(0.002)
    wkc, sw, pos, vel, m = send_cycle(0x0000)
    print(f"[*] 復位後 SW=0x{sw:04X}", flush=True)

# 步驟 1: 順序使能 (0x0006 -> 0x0007 -> 0x000F)
print("\n[步驟 1] 依序下發 Servo-On 使能...", flush=True)
for _ in range(100):
    send_cycle(0x0006)
    time.sleep(0.002)
_, sw, _, _, _ = send_cycle(0x0006)
print(f"[*] 0x0006 送出: SW=0x{sw:04X}", flush=True)

for _ in range(80):
    send_cycle(0x0007)
    time.sleep(0.002)
_, sw, _, _, _ = send_cycle(0x0007)
print(f"[*] 0x0007 送出: SW=0x{sw:04X}", flush=True)

print("[*] 下發 0x000F，等待 1.5 秒充磁吸合...", flush=True)
for _ in range(750): # 1.5 秒
    send_cycle(0x000F)
    time.sleep(0.002)

_, sw, pos, _, m = send_cycle(0x000F)
print(f"[+] 使能完成！SW=0x{sw:04X} | 模式={m} | 穩定位置={pos}", flush=True)

# 步驟 2: A/B 測試下發 0x001F (原廠速度 0 / 10 下)
print("\n[步驟 2] 下發 CW=0x001F 啟動 Homing (原廠速度: 6099:01=0, 6099:02=10)...", flush=True)
t_start = time.time()
while time.time() - t_start < 5.0:
    wkc, sw, pos, vel, m = send_cycle(0x001F)
    attained = bool(sw & 0x1000)
    fault    = bool(sw & 0x0008)
    error    = bool(sw & 0x2000)
    
    print(f"\r  SW: 0x{sw:04X} | 位置: {pos:6d} | 轉速: {vel:4d} | Attained: {attained}", end="", flush=True)
    
    if fault or error:
        print(f"\n[-] 檢測到異常終止！SW=0x{sw:04X}", flush=True)
        break
    time.sleep(0.01)

print("\n[*] 降使能退出...", flush=True)
for _ in range(50):
    send_cycle(0x0000)
    time.sleep(0.002)

slave.output = bytes(len(slave.output))
master.send_processdata()
master.close()
print("[+] 測試腳本安全退出完成。", flush=True)
