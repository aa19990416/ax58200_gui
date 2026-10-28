import pysoem
import struct
import time

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到 EtherCAT 從站！")
    exit()

slave = master.slaves[0]
print(f"[+] 找到從站: {slave.name}")

slave.state = pysoem.PREOP_STATE
master.write_state()
master.state_check(pysoem.PREOP_STATE, 50000)
time.sleep(0.1)

def sdo_r_s8(idx, sub):
    try:
        return struct.unpack("<b", slave.sdo_read(idx, sub))[0]
    except Exception as e:
        return f"Fail: {e}"

def sdo_w_s8(idx, sub, val):
    try:
        slave.sdo_write(idx, sub, struct.pack("<b", val))
        return True
    except Exception as e:
        return f"Fail: {e}"

cur_method = sdo_r_s8(0x6098, 0)
print(f"[*] 當前 0x6098 (Homing Method) = {cur_method}")

if cur_method == 0 or cur_method == "Fail":
    print("[*] 正在將 Homing Method 設定為 34 (尋找 Z 相 Index Pulse)...")
    ok = sdo_w_s8(0x6098, 0, 34)
    print(f"    寫入 Method 34 結果: {ok}")
    print(f"    寫入後讀回: {sdo_r_s8(0x6098, 0)}")

master.close()
