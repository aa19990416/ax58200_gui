import pysoem
import struct

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到從站！")
    exit()

slave = master.slaves[0]
print(f"[+] 連接從站: {slave.name}")

def r_sdo(idx, sub, name, fmt="<I"):
    try:
        raw = slave.sdo_read(idx, sub)
        val = struct.unpack(fmt, raw)[0]
        print(f"  0x{idx:04X}:{sub:<2d} | {name:<32} = {val} ({hex(val)})")
        return val
    except Exception as e:
        print(f"  0x{idx:04X}:{sub:<2d} | {name:<32} = 讀取失敗: {e}")
        return None

print("\n--- 1. 檢查同步模式與週期配置 (0x1C32 / 0x1C33) ---")
r_sdo(0x1C32, 1, "SM2 Sync Type", "<H")
r_sdo(0x1C32, 2, "SM2 Cycle Time (ns)")
r_sdo(0x1C32, 4, "SM2 Sync Types Supported", "<H")
r_sdo(0x1C32, 5, "SM2 Minimum Cycle Time (ns)")
r_sdo(0x1C32, 11, "SM2 SM-Event Missed Counter", "<H")
r_sdo(0x1C32, 12, "SM2 Cycle Exceeded Counter", "<H")

r_sdo(0x1C33, 1, "SM3 Sync Type", "<H")
r_sdo(0x1C33, 2, "SM3 Cycle Time (ns)")
r_sdo(0x1C33, 11, "SM3 SM-Event Missed Counter", "<H")

print("\n--- 2. 檢查馬達狀態與錯誤 ---")
r_sdo(0x6041, 0, "Statusword (SDO)", "<H")
r_sdo(0x603F, 0, "Error Code (SDO)", "<H")
r_sdo(0x6061, 0, "Modes of Op Display (SDO)", "<b")

master.close()
