import pysoem
import time
import struct

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到從站！")
    exit()

slave = master.slaves[0]
print(f"[+] 找到從站: {slave.name}")

def r_sdo(idx, sub, name, fmt="<I"):
    try:
        raw = slave.sdo_read(idx, sub)
        if fmt == "<H":
            val = struct.unpack("<H", raw)[0]
            print(f"  [0x{idx:04X}:{sub:<2d}] {name:<35} = {val} (0x{val:04X})")
        elif fmt == "<h":
            val = struct.unpack("<h", raw)[0]
            print(f"  [0x{idx:04X}:{sub:<2d}] {name:<35} = {val}")
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

def w_sdo(idx, sub, name, val, fmt="<H"):
    try:
        data = struct.pack(fmt, val)
        slave.sdo_write(idx, sub, data)
        print(f"  [+] 成功寫入 [0x{idx:04X}:{sub}] {name} = {val} ({hex(val)})")
        return True
    except Exception as e:
        print(f"  [-] 寫入失敗 [0x{idx:04X}:{sub}] {name}: {e}")
        return False

print("\n--- 1. 測試 SDO 寫入 Controlword 後是否真的能讀回 ---")
w_sdo(0x6040, 0, "Controlword", 0x0006, "<H")
r_sdo(0x6040, 0, "Controlword 讀回驗證", "<H")
r_sdo(0x6041, 0, "Statusword", "<H")

print("\n--- 2. 檢查 CiA 402 軸與通訊相關重要物件 ---")
r_sdo(0x1000, 0, "Device Type (0x1000)")
r_sdo(0x1018, 1, "Vendor ID (0x1018:1)")
r_sdo(0x1018, 2, "Product Code (0x1018:2)")
r_sdo(0x1018, 3, "Revision Number (0x1018:3)")

print("\n--- 3. 檢查驅動器極限與安全設定 ---")
r_sdo(0x607D, 1, "Software Min Position Limit", "<i")
r_sdo(0x607D, 2, "Software Max Position Limit", "<i")
r_sdo(0x6065, 0, "Following Error Window", "<I")
r_sdo(0x605A, 0, "Quick Stop Option Code", "<h")
r_sdo(0x605B, 0, "Shutdown Option Code", "<h")
r_sdo(0x605C, 0, "Disable Operation Option Code", "<h")
r_sdo(0x605E, 0, "Fault Reaction Option Code", "<h")

master.close()
