import pysoem
import struct
import time

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到從站！請檢查網路介面與接線。")
    exit()

slave = master.slaves[0]
cfg_addr = slave.config_address
print(f"[+] 連線從站: {slave.name} (Configured Address: 0x{cfg_addr:04X})")

# 配置 Process Data Map
master.config_map()

# 輔助讀取 SDO 函式
def sdo_r(idx, sub, fmt="<H"):
    try:
        raw = slave.sdo_read(idx, sub)
        return struct.unpack(fmt, raw)[0]
    except Exception:
        return None

# 透過 master.fprd (Configured address physical read) 讀取 ESC 暫存器
def esc_read_bytes(reg_addr, length):
    try:
        return master.fprd(cfg_addr, reg_addr, length, 2000)
    except Exception:
        return None

def esc_read_u8(reg_addr):
    b = esc_read_bytes(reg_addr, 1)
    return struct.unpack("<B", b)[0] if b and len(b) >= 1 else None

def esc_read_u16(reg_addr):
    b = esc_read_bytes(reg_addr, 2)
    return struct.unpack("<H", b)[0] if b and len(b) >= 2 else None

def esc_read_u32(reg_addr):
    b = esc_read_bytes(reg_addr, 4)
    return struct.unpack("<I", b)[0] if b and len(b) >= 4 else None

print("\n" + "=" * 70)
print("1. ESC 狀態機與錯誤狀態 (Registers 0x0120 ~ 0x0135)")
print("=" * 70)
al_ctrl = esc_read_u16(0x0120)
al_stat = esc_read_u16(0x0130)
al_stat_code = esc_read_u16(0x0134)
print(f"  AL Control       (0x0120) : {f'0x{al_ctrl:04X}' if al_ctrl is not None else '讀取失敗'}")
print(f"  AL Status        (0x0130) : {f'0x{al_stat:04X}' if al_stat is not None else '讀取失敗'}")
print(f"  AL Status Code   (0x0134) : {f'0x{al_stat_code:04X}' if al_stat_code is not None else '讀取失敗'}")

print("\n" + "=" * 70)
print("2. PDI 與中斷遮罩配置 (Registers 0x0140 ~ 0x0223)")
print("=" * 70)
pdi_ctrl = esc_read_u8(0x0140)
al_event_req = esc_read_u16(0x0220)
al_event_mask = esc_read_u16(0x0204)
print(f"  PDI Control      (0x0140) : {f'0x{pdi_ctrl:02X}' if pdi_ctrl is not None else '讀取失敗'}")
print(f"  AL Event Request (0x0220) : {f'0x{al_event_req:04X}' if al_event_req is not None else '讀取失敗'}")
print(f"  AL Event Mask    (0x0204) : {f'0x{al_event_mask:04X}' if al_event_mask is not None else '讀取失敗'} (若為 0 則 MCU 永遠收不到 PDI 中斷)")

print("\n" + "=" * 70)
print("3. SyncManager 2 (Outputs / RxPDO) 物理暫存器狀態 (0x0810 ~ 0x0817)")
print("=" * 70)
sm2_addr = esc_read_u16(0x0810)
sm2_len  = esc_read_u16(0x0812)
sm2_ctrl = esc_read_u8(0x0814)
sm2_stat = esc_read_u8(0x0815)
sm2_act  = esc_read_u8(0x0816)
sm2_pdi  = esc_read_u8(0x0817)
print(f"  SM2 Physical Start Addr (0x0810) : {f'0x{sm2_addr:04X}' if sm2_addr is not None else 'Fail'}")
print(f"  SM2 Length              (0x0812) : {sm2_len} Bytes")
print(f"  SM2 Control Register    (0x0814) : {f'0x{sm2_ctrl:02X}' if sm2_ctrl is not None else 'Fail'}")
print(f"  SM2 Status Register     (0x0815) : {f'0x{sm2_stat:02X}' if sm2_stat is not None else 'Fail'}")
print(f"  SM2 Activate Register   (0x0816) : {f'0x{sm2_act:02X}' if sm2_act is not None else 'Fail'} (Bit 0 必須為 1 才是 Active)")
print(f"  SM2 PDI Control         (0x0817) : {f'0x{sm2_pdi:02X}' if sm2_pdi is not None else 'Fail'}")

print("\n" + "=" * 70)
print("4. SyncManager 3 (Inputs / TxPDO) 物理暫存器狀態 (0x0818 ~ 0x081F)")
print("=" * 70)
sm3_addr = esc_read_u16(0x0818)
sm3_len  = esc_read_u16(0x081A)
sm3_ctrl = esc_read_u8(0x081C)
sm3_stat = esc_read_u8(0x081D)
sm3_act  = esc_read_u8(0x081E)
sm3_pdi  = esc_read_u8(0x081F)
print(f"  SM3 Physical Start Addr (0x0818) : {f'0x{sm3_addr:04X}' if sm3_addr is not None else 'Fail'}")
print(f"  SM3 Length              (0x081A) : {sm3_len} Bytes")
print(f"  SM3 Control Register    (0x081C) : {f'0x{sm3_ctrl:02X}' if sm3_ctrl is not None else 'Fail'}")
print(f"  SM3 Status Register     (0x081D) : {f'0x{sm3_stat:02X}' if sm3_stat is not None else 'Fail'}")
print(f"  SM3 Activate Register   (0x081E) : {f'0x{sm3_act:02X}' if sm3_act is not None else 'Fail'} (Bit 0 必須為 1 才是 Active)")
print(f"  SM3 PDI Control         (0x081F) : {f'0x{sm3_pdi:02X}' if sm3_pdi is not None else 'Fail'}")

print("\n" + "=" * 70)
print("5. DC 分散式時鐘狀態 (Registers 0x0980 ~ 0x098F)")
print("=" * 70)
dc_act = esc_read_u8(0x0980)
dc_pulse = esc_read_u16(0x0982)
dc_sync0_cyc = esc_read_u32(0x09A0)
dc_sync1_cyc = esc_read_u32(0x09A4)
print(f"  DC Cyclic Operation (0x0980)     : {f'0x{dc_act:02X}' if dc_act is not None else 'Fail'}")
print(f"  DC Pulse Length      (0x0982)     : {dc_pulse}")
print(f"  SYNC0 Cycle Time     (0x09A0)     : {dc_sync0_cyc} ns")
print(f"  SYNC1 Cycle Time     (0x09A4)     : {dc_sync1_cyc} ns")

print("\n" + "=" * 70)
print("6. CoE 同步參數物件 (0x1C32: SM2 Outputs / 0x1C33: SM3 Inputs)")
print("=" * 70)
for name, idx in [("SM2 Outputs 同步 (0x1C32)", 0x1C32), ("SM3 Inputs 同步 (0x1C33)", 0x1C33)]:
    sync_type = sdo_r(idx, 1, "<H")
    cycle_time = sdo_r(idx, 2, "<I")
    min_cycle  = sdo_r(idx, 3, "<I")
    sync_type_desc = {0: "Free Run", 1: "SM-Synchron", 2: "DC-Synchron (SYNC0)", 3: "DC-Synchron (SYNC1)"}.get(sync_type, "未知")
    print(f"  [{name}]")
    print(f"    Sub 1 (Sync Type)      : {sync_type} ({sync_type_desc})")
    print(f"    Sub 2 (Cycle Time)     : {cycle_time} ns")
    print(f"    Sub 3 (Min Cycle Time) : {min_cycle} ns")

master.close()
print("\n[*] ESC 暫存器與同步參數深度診斷完成。")
