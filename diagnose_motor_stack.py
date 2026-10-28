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

master.config_map()

def sdo_r_u8(idx, sub):
    try:
        raw = slave.sdo_read(idx, sub)
        return struct.unpack("<B", raw)[0]
    except Exception:
        return None

def sdo_r_i8(idx, sub):
    try:
        raw = slave.sdo_read(idx, sub)
        return struct.unpack("<b", raw)[0]
    except Exception:
        return None

def sdo_r_u16(idx, sub):
    try:
        raw = slave.sdo_read(idx, sub)
        return struct.unpack("<H", raw)[0]
    except Exception:
        return None

def sdo_r_u32(idx, sub):
    try:
        raw = slave.sdo_read(idx, sub)
        return struct.unpack("<I", raw)[0]
    except Exception:
        return None

print("\n" + "=" * 70)
print("1. CiA 402 軸參數與運作模式診斷")
print("=" * 70)

mode_set = sdo_r_i8(0x6060, 0)
mode_act = sdo_r_i8(0x6061, 0)
v_dc     = sdo_r_u32(0x6079, 0)
di_stat  = sdo_r_u32(0x60FD, 0)

mode_map = {1: "PP (Profile Position)", 3: "PV (Profile Velocity)", 4: "PT (Profile Torque)", 6: "Homing", 8: "CSP (Cyclic Sync Position)", 9: "CSV (Cyclic Sync Velocity)", 10: "CST (Cyclic Sync Torque)"}

print(f"  0x6060 Modes of operation         : {mode_set} ({mode_map.get(mode_set, '未設定/自定義')})")
print(f"  0x6061 Modes of operation display : {mode_act} ({mode_map.get(mode_act, '未啟用/自定義')})")
print(f"  0x6079 DC Link Voltage            : {f'{v_dc/1000.0:.2f} V' if v_dc is not None else '不支援讀取'}")
print(f"  0x60FD Digital Inputs             : {f'0x{di_stat:08X}' if di_stat is not None else 'Fail'}")

print("\n" + "=" * 70)
print("2. 檢查製造商自定義與驅動器保護物件 (0x2000 ~ 0x2020)")
print("=" * 70)
for idx in [0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2010, 0x2020]:
    val = sdo_r_u32(idx, 0)
    if val is not None:
        print(f"  物件 0x{idx:04X}:00 = 0x{val:08X} ({val})")

# 3. 測試在指定模式 (CSP = 8) 後再次測試 Fault Reset
print("\n" + "=" * 70)
print("3. 設定 0x6060 = 8 (CSP 模式) 並嘗試寫入 Fault Reset")
print("=" * 70)
try:
    slave.sdo_write(0x6060, 0, struct.pack("<b", 8))
    time.sleep(0.1)
    new_mode = sdo_r_i8(0x6060, 0)
    print(f"  設定後 0x6060 模式: {new_mode}")
except Exception as e:
    print(f"  寫入 0x6060 失敗: {e}")

master.close()
print("\n[*] 診斷完畢。")
