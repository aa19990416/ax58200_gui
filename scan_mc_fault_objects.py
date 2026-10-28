import pysoem
import struct

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到從站！")
    exit()

slave = master.slaves[0]
print(f"[+] 找到從站: {slave.name}")

def sdo_read_any(idx, sub):
    try:
        raw = slave.sdo_read(idx, sub)
        if len(raw) == 1:
            return struct.unpack("<B", raw)[0], "uint8"
        elif len(raw) == 2:
            return struct.unpack("<H", raw)[0], "uint16"
        elif len(raw) == 4:
            return struct.unpack("<I", raw)[0], "uint32"
        return raw.hex(), "raw"
    except Exception:
        return None, None

print("\n" + "=" * 70)
print("1. 標準 CiA 402 硬體與保護暫存器掃描")
print("=" * 70)

std_objs = [
    (0x603F, 0, "Error Code"),
    (0x6041, 0, "Statusword"),
    (0x6061, 0, "Modes of Operation Display"),
    (0x6079, 0, "DC Link Circuit Voltage (mV)"),
    (0x60FD, 0, "Digital Inputs (Bit 0=NegLim, Bit 1=PosLim, Bit 2=Home)"),
    (0x1001, 0, "Error Register"),
    (0x1003, 0, "Pre-defined Error Field: Count"),
    (0x1003, 1, "Pre-defined Error Field: Standard Error 1"),
]

for idx, sub, desc in std_objs:
    val, t = sdo_read_any(idx, sub)
    if val is not None:
        if isinstance(val, int):
            print(f"  0x{idx:04X}:{sub:02d} | 0x{val:08X} ({val:<8}) | {desc}")
        else:
            print(f"  0x{idx:04X}:{sub:02d} | {val:<18} | {desc}")

print("\n" + "=" * 70)
print("2. 掃描 ASIX 製造商私有物件 (0x2000 ~ 0x2020)")
print("=" * 70)

for idx in range(0x2000, 0x2021):
    val, t = sdo_read_any(idx, 0)
    if val is not None:
        print(f"  [發現物件] 0x{idx:04X}:00 -> 0x{val:08X} ({val}) [{t}]")
        for sub in range(1, 8):
            sub_val, sub_t = sdo_read_any(idx, sub)
            if sub_val is not None:
                print(f"    └─ Sub {sub:02d} -> 0x{sub_val:08X} ({sub_val}) [{sub_t}]")

print("\n" + "=" * 70)
print("3. MC Fault Status 故障位元對照表（供比對參考）")
print("=" * 70)
fault_bit_defs = {
    0x0001: "MCFS_OVER_VOLT   (直流母線過壓)",
    0x0002: "MCFS_UNDER_VOLT  (直流母線欠壓 - 常見於主電源未開，僅供 3.3V/5V 邏輯電)",
    0x0004: "MCFS_OVER_TEMP   (功率模組過溫)",
    0x0008: "MCFS_PHASE_ERR   (馬達 U/V/W 缺相或斷線)",
    0x0010: "MCFS_OVER_CURR   (過電流保護)",
    0x0020: "MCFS_SW_ERR      (軟體/算力逾時異常)",
    0x0040: "MCFS_VEC_ALI_ERR (向量初始對齊失敗)",
    0x0080: "MCFS_ENC_ALI_ERR (編碼器校正失敗)",
    0x0100: "MCFS_ENCODER_ERR (編碼器通訊/ABZ 信號遺失)",
    0x0200: "MCFS_PARAM_ERR   (馬達極對數/參數無效)",
}

for mask, desc in fault_bit_defs.items():
    print(f"  Bit 遮罩 0x{mask:04X} : {desc}")

master.close()
print("\n[*] 掃描完成。")
