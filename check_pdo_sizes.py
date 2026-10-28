import pysoem

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到從站！")
    exit()

slave = master.slaves[0]
print(f"[+] 從站名稱: {slave.name}")

# 執行映射
io_map_size = master.config_map()

print(f"[*] Master config_map 配置的總 IO Map 大小: {io_map_size} Bytes")
print(f"[*] Slave Outputs 長度 (RxPDO, Master->Slave): {len(slave.output)} Bytes (Bits: {slave.Obits})")
print(f"[*] Slave Inputs 長度  (TxPDO, Slave->Master): {len(slave.input)} Bytes (Bits: {slave.Ibits})")

master.close()
