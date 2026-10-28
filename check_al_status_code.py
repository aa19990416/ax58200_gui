import pysoem
import struct

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到從站！")
    exit()

slave = master.slaves[0]
print(f"[+] 從站名稱: {slave.name}")
print(f"[*] AL Status (0x0130): {hex(slave.al_status)}")

# 讀取 AL Status Code (暫存器 0x0134)
try:
    code = slave.al_status_code
    print(f"[*] AL Status Code (0x0134): {hex(code)}")
except Exception as e:
    # 嘗試直接讀取暫存器
    print(f"[-] 透過屬性讀取失敗: {e}")

# 檢查同步管理器狀態
for sm_idx in range(4):
    try:
        sm_stat = slave.sdo_read(0x1C00, sm_idx + 1)
        print(f"[*] SM{sm_idx} 狀態: {sm_stat}")
    except Exception:
        pass

master.close()
