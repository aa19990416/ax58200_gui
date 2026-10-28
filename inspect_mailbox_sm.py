import pysoem
import struct

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到從站！")
    exit()

slave = master.slaves[0]
print(f"[+] 找到從站: {slave.name}")

def read_u16(addr):
    try:
        raw = slave._fprd(addr, 2)
        return struct.unpack("<H", raw)[0]
    except Exception:
        return None

def read_u8(addr):
    try:
        raw = slave._fprd(addr, 1)
        return struct.unpack("<B", raw)[0]
    except Exception:
        return None

print("\n" + "=" * 70)
print("ESC Mailbox SyncManager 實體暫存器配置 (SM0 & SM1)")
print("=" * 70)

# SM0: Mailbox Write (Master -> Slave)
sm0_addr = read_u16(0x0800)
sm0_len  = read_u16(0x0802)
sm0_ctrl = read_u8(0x0804)
sm0_act  = read_u8(0x0806)

# SM1: Mailbox Read (Slave -> Master)
sm1_addr = read_u16(0x0808)
sm1_len  = read_u16(0x080A)
sm1_ctrl = read_u8(0x080C)
sm1_act  = read_u8(0x080E)

print("SM0 (Mailbox Out / Master to Slave):")
print(f"  Start Address    : {f'0x{sm0_addr:04X}' if sm0_addr is not None else 'Fail'}")
print(f"  Length           : {sm0_len} Bytes" if sm0_len is not None else "  Length: Fail")
print(f"  Control Register : {f'0x{sm0_ctrl:02X}' if sm0_ctrl is not None else 'Fail'}")
print(f"  Activate         : {f'0x{sm0_act:02X}' if sm0_act is not None else 'Fail'}")

print("\nSM1 (Mailbox In / Slave to Master):")
print(f"  Start Address    : {f'0x{sm1_addr:04X}' if sm1_addr is not None else 'Fail'}")
print(f"  Length           : {sm1_len} Bytes" if sm1_len is not None else "  Length: Fail")
print(f"  Control Register : {f'0x{sm1_ctrl:02X}' if sm1_ctrl is not None else 'Fail'}")
print(f"  Activate         : {f'0x{sm1_act:02X}' if sm1_act is not None else 'Fail'}")

# 檢查 SII EEPROM 中定義的標準 Mailbox 參數
print("\n從站物件記憶體中的 Mailbox 參數宣告 (來自 EEPROM):")
for attr in ['mbx_l', 'mbx_ro', 'mbx_rl', 'mbx_wo']:
    if hasattr(slave, attr):
        print(f"  slave.{attr} = {getattr(slave, attr)}")

master.close()
