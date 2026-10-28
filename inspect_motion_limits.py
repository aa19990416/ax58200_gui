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

def sdo_read_val(idx, sub, fmt):
    try:
        raw = slave.sdo_read(idx, sub)
        return struct.unpack(fmt, raw)[0]
    except Exception as e:
        return f"Fail ({e})"

targets = [
    (0x6072, 0, "<H", "Max Torque (千分比)"),
    (0x6073, 0, "<H", "Max Current (千分比)"),
    (0x6075, 0, "<I", "Motor Rated Current (mA)"),
    (0x607F, 0, "<I", "Max Profile Velocity"),
    (0x6080, 0, "<I", "Max Motor Speed (rpm)"),
    (0x6083, 0, "<I", "Profile Acceleration"),
    (0x6084, 0, "<I", "Profile Deceleration"),
    (0x6085, 0, "<I", "Quick Stop Deceleration"),
    (0x6098, 0, "<b", "Homing Method"),
    (0x6099, 1, "<I", "Homing Speed: Switch search"),
    (0x6099, 2, "<I", "Homing Speed: Zero search"),
    (0x609A, 0, "<I", "Homing Acceleration"),
]

print("\n" + "=" * 70)
print("CiA 402 核心運動限制與 Homing 配置檢查")
print("=" * 70)

for idx, sub, fmt, desc in targets:
    val = sdo_read_val(idx, sub, fmt)
    print(f"  0x{idx:04X}:{sub:02d} | {desc:<30} = {val}")

master.close()
