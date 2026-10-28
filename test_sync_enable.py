import time
import struct
import pysoem
from core.pdo_struct import PdoHandler

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到從站！")
    exit()

slave = master.slaves[0]
master.config_map()

# 配置 1ms (1,000,000 ns) DC 同步
cycle_ns = 1_000_000
try:
    slave.dc_sync(True, cycle_ns)
    master.config_dc()
except Exception as e:
    print(f"[!] DC 提示: {e}")

master.state_check(pysoem.SAFEOP_STATE, 50000)

# 寫入同步週期至 0x1C32 / 0x1C33
try:
    slave.sdo_write(0x1C32, 2, struct.pack("<I", cycle_ns))
    slave.sdo_write(0x1C33, 2, struct.pack("<I", cycle_ns))
except Exception:
    pass

slave.state = pysoem.OP_STATE
master.write_state()
master.state_check(pysoem.OP_STATE, 50000)

print(f"[+] 從站進入 OP 狀態: {slave.name}")

# 連續運算 2000 個週期 (2秒)，期間讓 DC 時鐘穩定並依序推進
for i in range(2500):
    t0 = time.perf_counter()
    
    # 根據執行時間推進指令
    if i < 500:
        cw = 0x0080  # 故障復位脈衝
    elif i < 1000:
        cw = 0x0006  # Shutdown (嘗試進入 0x1221)
    elif i < 1500:
        cw = 0x0007  # Switch On (嘗試進入 0x1223)
    else:
        cw = 0x000F  # Enable (嘗試進入 0x1227)

    # 讀取編碼器位置
    tx = PdoHandler.unpack_tx(slave.input) if slave.input else {'statusword': 0, 'actual_position': 0}
    act_pos = tx.get('actual_position', 0)
    sw = tx.get('statusword', 0)

    slave.output = PdoHandler.pack_rx(cw, act_pos, 0, 0)
    master.send_processdata()
    master.receive_processdata(2000)

    if i % 250 == 0:
        print(f"Cycle {i:4d} | CW={hex(cw)} | SW={hex(sw)} | Pos={act_pos}")

    elapsed = time.perf_counter() - t0
    sleep_t = 0.001 - elapsed
    if sleep_t > 0:
        time.sleep(sleep_t)

master.close()
print(f"最終 Statusword: {hex(sw)}")
