import pysoem
import struct

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到從站！")
    exit()

slave = master.slaves[0]
print(f"[+] 連接從站: {slave.name}")

# 1. 讀取從站 AL Status Code (0x0134) 查看是否有退回或內部報錯
try:
    al_status_code = slave.al_status
    print(f"[*] 當前從站狀態 (AL Status): {hex(slave.state)}")
except Exception as e:
    print(f"[-] 讀取狀態失敗: {e}")

# 2. 檢查 0x1C12 / 0x1C13 PDO 分配條目數
try:
    sm2_cnt = struct.unpack("<B", slave.sdo_read(0x1C12, 0))[0]
    sm3_cnt = struct.unpack("<B", slave.sdo_read(0x1C13, 0))[0]
    print(f"[*] 0x1C12 (RxPDO 分配數): {sm2_cnt}, 0x1C13 (TxPDO 分配數): {sm3_cnt}")
    if sm2_cnt > 0:
        pdo1600 = struct.unpack("<H", slave.sdo_read(0x1C12, 1))[0]
        print(f"    0x1C12:01 指向 PDO: 0x{pdo1600:04X}")
    if sm3_cnt > 0:
        pdo1a00 = struct.unpack("<H", slave.sdo_read(0x1C13, 1))[0]
        print(f"    0x1C13:01 指向 PDO: 0x{pdo1a00:04X}")
except Exception as e:
    print(f"[-] 讀取 PDO 分配失敗: {e}")

# 3. 檢查 0x6040 (Controlword) 與 0x6041 (Statusword)
try:
    cw = struct.unpack("<H", slave.sdo_read(0x6040, 0))[0]
    sw = struct.unpack("<H", slave.sdo_read(0x6041, 0))[0]
    err = struct.unpack("<H", slave.sdo_read(0x603F, 0))[0]
    print(f"[*] 0x6040 Controlword: 0x{cw:04X}")
    print(f"[*] 0x6041 Statusword:  0x{sw:04X}")
    print(f"[*] 0x603F Error Code:  0x{err:04X}")
except Exception as e:
    print(f"[-] 讀取 0x6040/0x6041 失敗: {e}")

master.close()
