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
    except Exception as e:
        return None

def sdo_r_u16(idx, sub):
    try:
        raw = slave.sdo_read(idx, sub)
        return struct.unpack("<H", raw)[0]
    except Exception as e:
        return None

def sdo_r_u32(idx, sub):
    try:
        raw = slave.sdo_read(idx, sub)
        return struct.unpack("<I", raw)[0]
    except Exception as e:
        return None

def sdo_w_u16(idx, sub, val):
    slave.sdo_write(idx, sub, struct.pack("<H", val))

print("\n" + "=" * 65)
print("1. 診斷馬達底層故障狀態 (Fault 狀態分析)")
print("=" * 65)

err_code = sdo_r_u16(0x603F, 0)
err_reg  = sdo_r_u8(0x1001, 0)
sw       = sdo_r_u16(0x6041, 0)
di       = sdo_r_u32(0x60FD, 0)

print(f"  0x6041 Statusword        : 0x{sw:04X} (Bit 3 = 1 代表 Fault)")
print(f"  0x603F Error Code        : {f'0x{err_code:04X}' if err_code is not None else '讀取失敗'}")
print(f"  0x1001 Error Register    : {f'0x{err_reg:02X}' if err_reg is not None else '讀取失敗'}")
print(f"  0x60FD Digital Inputs    : {f'0x{di:08X}' if di is not None else '讀取失敗'}")

# 檢查歷史故障清單 0x1003
num_errs = sdo_r_u8(0x1003, 0)
print(f"\n  0x1003:00 記錄的錯誤數量  : {num_errs if num_errs is not None else '無記錄/不支援'}")
if num_errs and num_errs > 0:
    for i in range(1, min(num_errs + 1, 6)):
        h_err = sdo_r_u32(0x1003, i)
        print(f"    [錯誤 #{i}] 0x{h_err:08X}")

print("\n" + "=" * 65)
print("2. 嘗試透過 SDO 下發 Fault Reset (0x0000 -> 0x0080 -> 0x0000)")
print("=" * 65)

sdo_w_u16(0x6040, 0, 0x0000)
time.sleep(0.1)
sdo_w_u16(0x6040, 0, 0x0080)
time.sleep(0.3)
sw_after_reset = sdo_r_u16(0x6041, 0)
err_after_reset = sdo_r_u16(0x603F, 0)
print(f"  下發 0x0080 後 Statusword: 0x{sw_after_reset:04X} | Error Code: 0x{err_after_reset:04X}")

sdo_w_u16(0x6040, 0, 0x0000)
time.sleep(0.1)
sw_final = sdo_r_u16(0x6041, 0)
print(f"  清除 0x0080 後 Statusword: 0x{sw_final:04X}")

if sw_final & 0x0008:
    print("\n[!] 警告: Fault 無法透過常規 0x0080 復位，代表存在持續性硬體攔截 (如主電源未接通、STO 開路、極限觸發或編碼器異常)。")
else:
    print(f"\n[+] 成功清除 Fault！當前狀態機已脫離故障態 (SW = 0x{sw_final:04X})。")

master.close()
