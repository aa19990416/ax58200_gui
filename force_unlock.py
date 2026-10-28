import time
import pysoem
import struct
from core.pdo_struct import PdoHandler

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到從站！")
    exit()

slave = master.slaves[0]
master.config_map()

# 配置 1ms 分散式時鐘 (DC)
cycle_ns = 1_000_000
try:
    slave.dc_sync(True, cycle_ns)
    master.config_dc()
except Exception as e:
    pass

master.state_check(pysoem.SAFEOP_STATE, 50000)

slave.state = pysoem.OP_STATE
master.write_state()
master.state_check(pysoem.OP_STATE, 50000)

print(f"[+] 進入 OP 狀態，從站: {slave.name}")

# 關鍵序列：
# 1. 模式設為 0 (No Mode)
# 2. Controlword 先送 0x0080 (復位邊緣) -> 送 0x0000 (Disable) -> 送 0x0006 (Shutdown) -> 送 0x0007 -> 送 0x000F
steps = [
    (0x0080, 0, "Fault Reset 脈衝"),
    (0x0000, 0, "Disable Voltage (清空使能)"),
    (0x0006, 0, "Shutdown (準備切換至 Ready)"),
    (0x0007, 0, "Switch On (上電就緒)"),
    (0x000F, 0, "Enable Operation (激磁使能)"),
]

for cw, mode, desc in steps:
    print(f"[*] 發送 {desc}: Controlword={hex(cw)}, Mode={mode}")
    # 每個狀態維持發送 200 個週期 (200ms)
    for _ in range(200):
        # 讀取當前編碼器位置以保持位置同步
        tx = PdoHandler.unpack_tx(slave.input) if slave.input else {'actual_position': 0}
        act_pos = tx.get('actual_position', 0)
        
        slave.output = PdoHandler.pack_rx(cw, act_pos, 0, mode)
        master.send_processdata()
        wkc = master.receive_processdata(2000)
        time.sleep(0.001)

    # 讀取最新狀態
    tx = PdoHandler.unpack_tx(slave.input)
    sw = tx['statusword']
    pos = tx['actual_position']
    print(f"    -> Statusword: {hex(sw)} | 當前位置: {pos}")

master.close()
print("[*] 執行完成。")
