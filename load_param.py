import pysoem
import struct

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到從站！")
    exit()

slave = master.slaves[0]
print(f"[+] 連接從站: {slave.name}")

def read_sdo(index, sub, name, fmt="<I"):
    try:
        raw = slave.sdo_read(index, sub)
        val = struct.unpack(fmt, raw)[0]
        print(f"  [R] 0x{index:04X}:{sub} {name:<20} = {val} ({hex(val)})")
        return val
    except Exception as e:
        print(f"  [R] 0x{index:04X}:{sub} {name:<20} = 失敗: {e}")
        return None

def write_sdo(index, sub, val, fmt="<I", name=""):
    try:
        data = struct.pack(fmt, val)
        slave.sdo_write(index, sub, data)
        print(f"  [W] 0x{index:04X}:{sub} {name:<20} 寫入成功: {hex(val)}")
        return True
    except Exception as e:
        print(f"  [W] 0x{index:04X}:{sub} {name:<20} 寫入失敗: {e}")
        return False

print("\n--- 檢查目前版本 ---")
read_sdo(0x33FE, 0, "romVERS")
read_sdo(0x33FF, 0, "romDATE")

# 嘗試觸發標準 CiA 402 / 亞信參數重載
# 0x1010: Store parameters (寫入 "save" = 0x65766173)
# 0x1011: Restore parameters (寫入 "load" = 0x64616F6C)
print("\n--- 嘗試觸發參數載入 ---")
write_sdo(0x1010, 1, 0x65766173, "<I", "Store param (save)")
write_sdo(0x1011, 1, 0x64616F6C, "<I", "Restore param (load)")

master.close()
print("\n[*] 執行結束。請按一次板載 Reset 鍵，並查看 UART 開機日誌中的 sysVERS 是否改變。")
