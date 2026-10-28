import pysoem
import struct

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到從站！")
    exit()

slave = master.slaves[0]
print(f"[+] 連線從站: {slave.name}")

def r_sdo(idx, sub, name, fmt="<I"):
    try:
        raw = slave.sdo_read(idx, sub)
        if fmt == "<H":
            val = struct.unpack("<H", raw)[0]
            print(f"  [0x{idx:04X}:{sub:<2d}] {name:<35} = {val} (0x{val:04X})")
        elif fmt == "<b":
            val = struct.unpack("<b", raw)[0]
            print(f"  [0x{idx:04X}:{sub:<2d}] {name:<35} = {val}")
        elif fmt == "<B":
            val = struct.unpack("<B", raw)[0]
            print(f"  [0x{idx:04X}:{sub:<2d}] {name:<35} = {val} (0x{val:02X})")
        elif fmt == "<i":
            val = struct.unpack("<i", raw)[0]
            print(f"  [0x{idx:04X}:{sub:<2d}] {name:<35} = {val}")
        else:
            val = struct.unpack("<I", raw)[0]
            print(f"  [0x{idx:04X}:{sub:<2d}] {name:<35} = {val} (0x{val:08X})")
        return val
    except Exception as e:
        print(f"  [0x{idx:04X}:{sub:<2d}] {name:<35} = 讀取失敗: {e}")
        return None

print("\n--- 1. 馬達模式與核心變數狀態 ---")
r_sdo(0x6040, 0, "Controlword (0x6040)", "<H")
r_sdo(0x6041, 0, "Statusword (0x6041)", "<H")
r_sdo(0x6060, 0, "Modes of operation (0x6060)", "<b")
r_sdo(0x6061, 0, "Modes of operation display", "<b")
r_sdo(0x6064, 0, "Position actual value (0x6064)", "<i")
r_sdo(0x606C, 0, "Velocity actual value (0x606C)", "<i")
r_sdo(0x607A, 0, "Target position (0x607A)", "<i")
r_sdo(0x60FF, 0, "Target velocity (0x60FF)", "<i")

print("\n--- 2. 驅動器底層錯誤與警告 ---")
r_sdo(0x603F, 0, "Error code (0x603F)", "<H")
r_sdo(0x1001, 0, "Error register (0x1001)", "<B")

master.close()
