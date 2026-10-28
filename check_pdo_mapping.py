import pysoem
import struct

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到從站！")
    exit()

slave = master.slaves[0]
print(f"[+] 從站名稱: {slave.name}")

def inspect_pdo(pdo_idx, name):
    print(f"\n--- 檢查 {name} (0x{pdo_idx:04X}) 映射表 ---")
    try:
        num_entries = struct.unpack("<B", slave.sdo_read(pdo_idx, 0))[0]
        print(f"  映射條目數: {num_entries}")
        total_bits = 0
        for sub in range(1, num_entries + 1):
            val = struct.unpack("<I", slave.sdo_read(pdo_idx, sub))[0]
            obj_idx = (val >> 16) & 0xFFFF
            obj_sub = (val >> 8) & 0xFF
            bit_len = val & 0xFF
            total_bits += bit_len
            print(f"  Sub {sub}: 映射物件 0x{obj_idx:04X}:{obj_sub:02X} | 長度: {bit_len:2d} bits ({bit_len//8} Bytes)")
        print(f"  總位元數: {total_bits} bits -> 總位元組數: {total_bits / 8} Bytes")
    except Exception as e:
        print(f"  讀取失敗: {e}")

inspect_pdo(0x1600, "RxPDO (主站送從站)")
inspect_pdo(0x1A00, "TxPDO (從站回主站)")

master.close()
