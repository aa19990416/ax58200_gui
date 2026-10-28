import pysoem
import struct

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到從站！")
    exit()

slave = master.slaves[0]
print(f"[+] 連接從站: {slave.name}")

def sdo_r(index, sub, fmt="<i"):
    try:
        raw = slave.sdo_read(index, sub)
        return struct.unpack(fmt, raw)[0]
    except Exception:
        return None

def sdo_w(index, sub, val, fmt="<i"):
    try:
        slave.sdo_write(index, sub, struct.pack(fmt, val))
        return True
    except Exception as e:
        return False

print("\n--- 1. 檢查目前讀值 ---")
print(f"  romVERS (0x33FE:0) = {sdo_r(0x33FE, 0)}")
print(f"  romDATE (0x33FF:0) = {sdo_r(0x33FF, 0)}")

# 嘗試寫入 0x3100 ~ 0x33FD 範圍內可能存在的 Flash 操作指令物件
# 亞信常見的 commit 指令: 寫入 1 或 0x65766173 ('save')
targets = [
    (0x3000, 0, "System Control"),
    (0x3001, 0, "Param Command"),
    (0x3100, 0, "Motor Config"),
    (0x3130, 0, "posMXE"),
    (0x31E5, 0, "angPOLE"),
]

for idx, sub, name in targets:
    val = sdo_r(idx, sub)
    if val is not None:
        print(f"  [可讀] 0x{idx:04X}:{sub} ({name}) = {val}")

# 關鍵：有些版本在切換模式時會強制同步參數
print("\n--- 2. 測試寫入 0x6060 (Modes of Operation) 並觀察反應 ---")
for m in [0, 8, 6]:
    sdo_w(0x6060, 0, m, "<b")
    sw = sdo_r(0x6041, 0, "<H")
    print(f"  設定 0x6060={m} -> Statusword (0x6041) = {hex(sw) if sw else 'None'}")

master.close()
