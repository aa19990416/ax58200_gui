import pysoem
import struct

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到從站！請檢查網路介面與接線。")
    exit()

slave = master.slaves[0]
print(f"[+] 找到從站: {slave.name}")

def sdo_read_param(idx, sub=0):
    try:
        raw = slave.sdo_read(idx, sub)
        length = len(raw)
        if length == 1:
            val = struct.unpack("<B", raw)[0]
            fmt = f"{val} (0x{val:02X})"
        elif length == 2:
            val = struct.unpack("<H", raw)[0]
            fmt = f"{val} (0x{val:04X})"
        elif length == 4:
            val = struct.unpack("<I", raw)[0]
            fmt = f"{val} (0x{val:08X})"
        else:
            fmt = raw.hex()
        return fmt, length
    except Exception as e:
        return None, None

targets = [
    (0x31D3, 0, "adcK0   - ADC Offset Compensation"),
    (0x31D4, 0, "adcK    - ADC Current Gain"),
    (0x31D5, 0, "adcSTD  - Motor Rated Current (原廠預設: 4)"),
    (0x31E4, 0, "angREL  - Encoder Resolution (原廠預設: 10000)"),
    (0x31E5, 0, "angPOLE - Motor Pole Pairs (原廠預設: 4)"),
    (0x31E7, 0, "angINV  - Angle Polarity / Invert (原廠預設: 3)"),
    (0x33FE, 0, "romVERS - Parameter File Version"),
    (0x33FF, 0, "romDATE - Parameter File Date"),
]

print("\n" + "=" * 75)
print(f"{'物件':<10} | {'長度':<6} | {'當前數值':<25} | {'說明'}")
print("=" * 75)

for idx, sub, desc in targets:
    val_str, length = sdo_read_param(idx, sub)
    if val_str is not None:
        print(f"0x{idx:04X}:{sub:02d} | {length}B    | {val_str:<25} | {desc}")
    else:
        print(f"0x{idx:04X}:{sub:02d} | Fail   | {'讀取失敗 (無此物件或拒絕存取)':<25} | {desc}")

master.close()
print("=" * 75)
print("[*] 讀取完成。")
