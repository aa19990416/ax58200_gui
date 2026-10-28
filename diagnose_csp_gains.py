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
time.sleep(0.2)

def sdo_r(idx, sub=0):
    try:
        raw = slave.sdo_read(idx, sub)
        if len(raw) == 1:
            return struct.unpack("<b", raw)[0]
        elif len(raw) == 2:
            return struct.unpack("<H", raw)[0]
        elif len(raw) == 4:
            return struct.unpack("<i", raw)[0]
        return raw.hex()
    except Exception:
        return None

print("\n" + "=" * 65)
print("1. 檢查 CiA 402 模式與即時反饋")
print("=" * 65)
m_set = sdo_r(0x6060, 0)
m_act = sdo_r(0x6061, 0)
sw    = sdo_r(0x6041, 0)
p_act = sdo_r(0x6064, 0)
p_tar = sdo_r(0x607A, 0)

print(f"  0x6060 (Modes of Operation Set)     : {m_set}")
print(f"  0x6061 (Modes of Operation Display) : {m_act} ({'CSP 正常' if m_act == 8 else '注意: 模式不吻合！'})")
print(f"  0x6041 (Statusword)                 : 0x{sw:04X}" if sw else "  0x6041: Fail")
print(f"  0x6064 (Position Actual Value)      : {p_act}")
print(f"  0x607A (Target Position)            : {p_tar}")

print("\n" + "=" * 65)
print("2. 檢查位置環/速度環/電流環參數 (0x31D0 ~ 0x31EF)")
print("=" * 65)
for idx in range(0x31D0, 0x31F0):
    val = sdo_r(idx, 0)
    if val is not None:
        print(f"  0x{idx:04X}:00 -> {val} (0x{val & 0xFFFFFFFF:X})")

master.close()
