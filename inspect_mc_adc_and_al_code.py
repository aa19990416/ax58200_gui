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

slave.state = pysoem.PREOP_STATE
master.write_state()
master.state_check(pysoem.PREOP_STATE, 50000)
time.sleep(0.3)
master.read_state()

def read_esc_u16(addr):
    try:
        raw = slave._fprd(addr, 2)
        return struct.unpack("<H", raw)[0]
    except Exception:
        return None

al_stat = read_esc_u16(0x0130)
al_code = read_esc_u16(0x0134)
print(f"\n[1] PREOP 階段 ESC 狀態: AL Status = 0x{al_stat:04X} | AL Code = 0x{al_code:04X}")

def sdo_read_any(idx, sub=0):
    try:
        raw = slave.sdo_read(idx, sub)
        if len(raw) == 1:
            return struct.unpack("<B", raw)[0]
        elif len(raw) == 2:
            return struct.unpack("<H", raw)[0]
        elif len(raw) == 4:
            return struct.unpack("<I", raw)[0]
        return raw.hex()
    except Exception:
        return None

print("\n" + "=" * 70)
print("[2] 掃描 0x31C0 ~ 0x31DF 區域 (ADC 採樣 / 電壓 / 電流 / 溫度 / MC 狀態)")
print("=" * 70)

for idx in range(0x31A0, 0x31EF):
    val = sdo_read_any(idx, 0)
    if val is not None:
        val_str = f"0x{val:08X} ({val})" if isinstance(val, int) else str(val)
        print(f"  0x{idx:04X}:00 -> {val_str}")

master.close()
