import pysoem
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
    except Exception:
        return None

print("\n--- 1. CiA 402 軸與馬達基本設定物件 ---")
r_sdo(0x60FD, 0, "Digital inputs (0x60FD)")
r_sdo(0x60FE, 1, "Physical outputs (0x60FE:1)")
r_sdo(0x60FE, 2, "Bit mask (0x60FE:2)")
r_sdo(0x607C, 0, "Home offset (0x607C)", "<i")
r_sdo(0x6085, 0, "Quick stop deceleration", "<I")
r_sdo(0x60C2, 1, "Interpolation time period", "<B")

print("\n--- 2. 廠商專屬物件掃描 (0x2000 ~ 0x2040, 0x3000 ~ 0x3020) ---")
found_mfg = False
for idx in list(range(0x2000, 0x2040)) + list(range(0x3000, 0x3020)):
    try:
        raw = slave.sdo_read(idx, 0)
        print(f"  [0x{idx:04X}:0] 發現自訂物件, 長度={len(raw)}B, 內容={raw.hex()}")
        found_mfg = True
    except Exception:
        pass

if not found_mfg:
    print("  [-] 0x2000/0x3000 範圍無開放之物件")

print("\n--- 3. 測試 0x60FD Digital Inputs 狀態 (是否有硬體極限開關或急停未接) ---")
raw_di = r_sdo(0x60FD, 0, "Digital Inputs Raw")
if raw_di is not None:
    # CiA 402 標準 0x60FD 定義:
    # Bit 0: negative limit switch
    # Bit 1: positive limit switch
    # Bit 2: home switch
    # Bit 3: interlock
    neg_lim = bool(raw_di & (1 << 0))
    pos_lim = bool(raw_di & (1 << 1))
    home_sw = bool(raw_di & (1 << 2))
    interlock = bool(raw_di & (1 << 3))
    print(f"  解析: Negative Limit={neg_lim}, Positive Limit={pos_lim}, Home Switch={home_sw}, Interlock={interlock}")

master.close()
