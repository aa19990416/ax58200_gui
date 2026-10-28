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

def sdo_r(idx, sub, fmt):
    try:
        d = slave.sdo_read(idx, sub)
        return struct.unpack(fmt, d[:struct.calcsize(fmt)])[0]
    except Exception as e:
        return f"Fail: {e}"

print("\n" + "=" * 55)
print("Homing 內部啟動條件與硬體鎖定診斷")
print("=" * 55)

# 1. 數位輸入開關 (檢查是否觸發極限開關)
din = sdo_r(0x60FD, 0, "<I")
print(f"1. 0x60FD (Digital Inputs)        : 0x{din:08X}" if isinstance(din, int) else f"1. 0x60FD: {din}")
if isinstance(din, int):
    # CiA 402 標準 0x60FD 位元定義:
    # bit 0: negative limit switch
    # bit 1: positive limit switch
    # bit 2: home switch
    # bit 3: interlock
    print(f"   - Negative Limit Switch (bit 0): {(din >> 0) & 1}")
    print(f"   - Positive Limit Switch (bit 1): {(din >> 1) & 1}")
    print(f"   - Home Switch           (bit 2): {(din >> 2) & 1}")
    print(f"   - Interlock             (bit 3): {(din >> 3) & 1}")

# 2. Homing 加速度與極限
acc = sdo_r(0x609A, 0, "<I")
print(f"2. 0x609A (Homing Acceleration)   : {acc}")

# 3. 最大轉速約束
max_vel = sdo_r(0x607F, 0, "<I")
print(f"3. 0x607F (Max Profile Velocity)  : {max_vel}")

# 4. 前次錯誤記錄
err_code = sdo_r(0x603F, 0, "<H")
print(f"4. 0x603F (Last Error Code)       : 0x{err_code:04X}" if isinstance(err_code, int) else f"4. 0x603F: {err_code}")

# 5. Method 35 支援檢查 (Method 35 是當前位置直接標定為原點，不需轉動)
print("=" * 55)

master.close()
