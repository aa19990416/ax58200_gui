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

def sdo_r(idx, sub):
    try:
        return struct.unpack("<I", slave.sdo_read(idx, sub))[0]
    except Exception as e:
        return f"Fail: {e}"

def sdo_w(idx, sub, val):
    try:
        slave.sdo_write(idx, sub, struct.pack("<I", val))
        return True
    except Exception as e:
        return f"Fail: {e}"

print("\n" + "=" * 55)
print("1. 寫入前檢查:")
print(f"   0x6099:01 (Switch Speed) = {sdo_r(0x6099, 1)}")
print(f"   0x6099:02 (Zero Speed)   = {sdo_r(0x6099, 2)}")

print("\n2. 執行 SDO 寫入 (設定為 600 rpm / 6000 counts)...")
w1 = sdo_w(0x6099, 1, 600)
w2 = sdo_w(0x6099, 2, 600)
print(f"   寫入 0x6099:01 結果: {w1}")
print(f"   寫入 0x6099:02 結果: {w2}")

print("\n3. 寫入後立即讀回驗證:")
print(f"   0x6099:01 讀回值 = {sdo_r(0x6099, 1)}")
print(f"   0x6099:02 讀回值 = {sdo_r(0x6099, 2)}")
print("=" * 55)

master.close()
