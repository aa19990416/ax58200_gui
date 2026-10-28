import pysoem
import time

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到從站！")
    exit()

slave = master.slaves[0]
master.config_map()

# 配置 1ms DC
cycle_ns = 1_000_000
try:
    slave.dc_sync(True, cycle_ns)
    master.config_dc()
except Exception:
    pass

master.state_check(pysoem.SAFEOP_STATE, 50000)
slave.state = pysoem.OP_STATE
master.write_state()
master.state_check(pysoem.OP_STATE, 50000)

print(f"[+] 從站進入 OP: {slave.name}")
print(f"[*] Input (TxPDO) 大小: {len(slave.input)} bytes, Output (RxPDO) 大小: {len(slave.output)} bytes")

# 連續發送 0x0006 並印出 Raw Hex
for i in range(20):
    slave.output = bytes(len(slave.output))  # 全 0 測試
    master.send_processdata()
    wkc = master.receive_processdata(2000)
    
    if wkc > 0:
        raw_in = slave.input
        print(f"TxPDO Raw Hex: {' '.join(f'{b:02X}' for b in raw_in)}")
    time.sleep(0.05)

master.close()
