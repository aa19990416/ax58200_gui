import pysoem
import struct

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到從站！請檢查網路介面與接線。")
    exit()

slave = master.slaves[0]
print(f"[+] 找到從站: {slave.name}")

# 將從站切至 PREOP 狀態以安全讀取物件字典
slave.state = pysoem.PREOP_STATE
master.write_state()
master.state_check(pysoem.PREOP_STATE, 50000)
print(f"[+] 從站處於 PREOP 狀態 (0x{slave.state:02X})")

def read_u8(idx, sub):
    try:
        raw = slave.sdo_read(idx, sub)
        return struct.unpack("<B", raw)[0]
    except Exception:
        return None

def read_u16(idx, sub):
    try:
        raw = slave.sdo_read(idx, sub)
        return struct.unpack("<H", raw)[0]
    except Exception:
        return None

def read_u32(idx, sub):
    try:
        raw = slave.sdo_read(idx, sub)
        return struct.unpack("<I", raw)[0]
    except Exception:
        return None

def dump_mapping(pdo_idx, direction):
    num_entries = read_u8(pdo_idx, 0)
    if num_entries is None or num_entries == 0:
        print(f"    -> 物件 0x{pdo_idx:04X} 無有效 Mapping 條目 (Sub 0 = {num_entries})")
        return 0
    
    print(f"    -> PDO 0x{pdo_idx:04X} ({direction}) 包含 {num_entries} 個 Mapping 條目:")
    total_bits = 0
    for i in range(1, num_entries + 1):
        entry = read_u32(pdo_idx, i)
        if entry is None:
            print(f"       Sub {i}: 讀取失敗")
            continue
        mapped_idx = (entry >> 16) & 0xFFFF
        mapped_sub = (entry >> 8) & 0xFF
        bit_len = entry & 0xFF
        total_bits += bit_len
        print(f"       [{i}] 目標物件: 0x{mapped_idx:04X}:{mapped_sub:<2d} | 長度: {bit_len:2d} bits ({bit_len // 8} Bytes)")
    
    print(f"       >> 總位元數: {total_bits} bits ({total_bits // 8} Bytes, 餘數: {total_bits % 8} bits)")
    return total_bits

print("\n" + "=" * 65)
print("1. 診斷 SyncManager 2 Assignment (0x1C12 -> RxPDO / Outputs)")
print("=" * 65)
num_sm2 = read_u8(0x1C12, 0)
print(f"SM2 (0x1C12:0) 指派的 RxPDO 數量: {num_sm2}")
total_rx_bits = 0
if num_sm2:
    for sub in range(1, num_sm2 + 1):
        pdo = read_u16(0x1C12, sub)
        if pdo:
            print(f"\n  [SM2 Sub {sub}] 指向 RxPDO: 0x{pdo:04X}")
            total_rx_bits += dump_mapping(pdo, "Master -> Slave")
        else:
            print(f"  [SM2 Sub {sub}] 讀取失敗")
else:
    print("  [!] 0x1C12:0 為 0 或不可讀，直接掃描預設 RxPDO 物件 (0x1600 ~ 0x1603):")
    for test_idx in range(0x1600, 0x1604):
        total_rx_bits += dump_mapping(test_idx, "Master -> Slave")

print("\n" + "=" * 65)
print("2. 診斷 SyncManager 3 Assignment (0x1C13 -> TxPDO / Inputs)")
print("=" * 65)
num_sm3 = read_u8(0x1C13, 0)
print(f"SM3 (0x1C13:0) 指派的 TxPDO 數量: {num_sm3}")
total_tx_bits = 0
if num_sm3:
    for sub in range(1, num_sm3 + 1):
        pdo = read_u16(0x1C13, sub)
        if pdo:
            print(f"\n  [SM3 Sub {sub}] 指向 TxPDO: 0x{pdo:04X}")
            total_tx_bits += dump_mapping(pdo, "Slave -> Master")
        else:
            print(f"  [SM3 Sub {sub}] 讀取失敗")
else:
    print("  [!] 0x1C13:0 為 0 或不可讀，直接掃描預設 TxPDO 物件 (0x1A00 ~ 0x1A03):")
    for test_idx in range(0x1A00, 0x1A04):
        total_tx_bits += dump_mapping(test_idx, "Slave -> Master")

print("\n" + "=" * 65)
print("3. pysoem 自動映射長度對比")
print("=" * 65)
io_size = master.config_map()
print(f"config_map() 總 IO 大小: {io_size} Bytes")
print(f"slave.output 緩衝區長度: {len(slave.output)} Bytes (對應總 RxPDO bits: {total_rx_bits})")
print(f"slave.input  緩衝區長度: {len(slave.input)} Bytes (對應總 TxPDO bits: {total_tx_bits})")

master.close()
print("\n[*] 診斷完畢。")
