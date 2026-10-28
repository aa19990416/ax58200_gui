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
            print(f"  [0x{idx:04X}:{sub:<2d}] {name:<32} = {val} (0x{val:04X})")
        elif fmt == "<h":
            val = struct.unpack("<h", raw)[0]
            print(f"  [0x{idx:04X}:{sub:<2d}] {name:<32} = {val}")
        elif fmt == "<b":
            val = struct.unpack("<b", raw)[0]
            print(f"  [0x{idx:04X}:{sub:<2d}] {name:<32} = {val}")
        elif fmt == "<B":
            val = struct.unpack("<B", raw)[0]
            print(f"  [0x{idx:04X}:{sub:<2d}] {name:<32} = {val} (0x{val:02X})")
        elif fmt == "<i":
            val = struct.unpack("<i", raw)[0]
            print(f"  [0x{idx:04X}:{sub:<2d}] {name:<32} = {val}")
        else:
            val = struct.unpack("<I", raw)[0]
            print(f"  [0x{idx:04X}:{sub:<2d}] {name:<32} = {val} (0x{val:08X})")
        return val
    except Exception as e:
        print(f"  [0x{idx:04X}:{sub:<2d}] {name:<32} = 讀取失敗: {e}")
        return None

print("\n--- 1. CiA 402 基本與模式物件 ---")
r_sdo(0x6040, 0, "Controlword (0x6040)", "<H")
r_sdo(0x6041, 0, "Statusword (0x6041)", "<H")
r_sdo(0x6060, 0, "Modes of operation (0x6060)", "<b")
r_sdo(0x6061, 0, "Modes of operation display", "<b")
r_sdo(0x6502, 0, "Supported drive modes (0x6502)")

print("\n--- 2. 錯誤代碼與詳細硬體故障 ---")
r_sdo(0x603F, 0, "Error code (0x603F)", "<H")
r_sdo(0x1001, 0, "Error register (0x1001)", "<B")
r_sdo(0x1003, 0, "Pre-defined error field cnt", "<B")
for i in range(1, 5):
    r_sdo(0x1003, i, f"Standard Error History {i}")

print("\n--- 3. 亞信 / 驅動器專屬參數 (電壓、電流、編碼器) ---")
# 讀取常見的製造商自訂母線電壓或驅動參數 (0x2000 ~ 0x2010 或 0x3000)
for idx in [0x2000, 0x2001, 0x2002, 0x2003, 0x2010, 0x2020, 0x3000, 0x3001]:
    r_sdo(idx, 0, f"Manufacturer Obj 0x{idx:04X}", "<I")

master.close()
