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
        print(f"  0x{idx:04X}:{sub} | {name:<25} = {val} ({hex(val)}) [bin: {bin(val)}]")
        return val
    except Exception as e:
        print(f"  0x{idx:04X}:{sub} | {name:<25} = 讀取失敗 ({e})")
        return None

print("\n--- 1. 硬體數位輸入狀態 (CiA 402 0x60FD) ---")
r_sdo(0x60FD, 0, "Digital Inputs (0x60FD)")

print("\n--- 2. 驅動器模式與狀態 ---")
r_sdo(0x6502, 0, "Supported drive modes")
r_sdo(0x6041, 0, "Statusword", "<H")
r_sdo(0x603F, 0, "Error code", "<H")

master.close()
