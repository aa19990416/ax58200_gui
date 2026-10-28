import pysoem

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到從站！請檢查連線。")
    exit()

slave = master.slaves[0]
print(f"[+] 從站名稱: {slave.name}")

# 執行總線 Process Data 映射
io_map_size = master.config_map()

print(f"[*] 總 IO Map 大小: {io_map_size} Bytes")
print(f"[*] slave.output (RxPDO, Master->Slave) 長度: {len(slave.output)} Bytes")
print(f"[*] slave.input  (TxPDO, Slave->Master) 長度: {len(slave.input)} Bytes")
print(f"[*] 當前 slave.input 原始內容 (Hex): {slave.input.hex()}")

master.close()
