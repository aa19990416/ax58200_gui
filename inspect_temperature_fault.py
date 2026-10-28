import pysoem
import struct
import time

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到從站！")
    exit()

slave = master.slaves[0]
print(f"[+] 找到從站: {slave.name}")

slave.state = pysoem.PREOP_STATE
master.write_state()
master.state_check(pysoem.PREOP_STATE, 50000)
time.sleep(0.3)

def sdo_r(idx, sub=0):
    try:
        raw = slave.sdo_read(idx, sub)
        return int.from_bytes(raw, byteorder='little'), len(raw)
    except Exception:
        return None, None

print("\n" + "=" * 70)
print("1. 診斷 0x31B1 Fault Status 與相關 ADC / 溫度物件")
print("=" * 70)

mcfs, _ = sdo_r(0x31B1, 0)
print(f"  0x31B1:00 (MC Fault Status) : 0x{mcfs:04X} ({mcfs})")
if mcfs & 0x0004:
    print("  [!!! 鎖定根因 !!!] Bit 2 = 1 -> MCFS_OVER_TEMP (過溫保護持續觸發！)")

for idx in range(0x31A0, 0x31C0):
    val, sz = sdo_r(idx, 0)
    if val is not None:
        print(f"  0x{idx:04X}:00 -> 0x{val:08X} ({val:<6}) [{sz} Bytes]")

master.close()
