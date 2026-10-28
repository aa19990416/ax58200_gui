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

# 配置 1ms 分散式時鐘 (DC)
cycle_ns = 1_000_000
try:
    slave.dc_sync(True, cycle_ns)
    master.config_dc()
except Exception as e:
    pass

master.state_check(pysoem.SAFEOP_STATE, 50000)

try:
    slave.sdo_write(0x1C32, 2, struct.pack("<I", cycle_ns))
    slave.sdo_write(0x1C33, 2, struct.pack("<I", cycle_ns))
except Exception:
    pass

slave.state = pysoem.OP_STATE
master.write_state()
master.state_check(pysoem.OP_STATE, 50000)

print(f"[+] 進入 OP 狀態: {slave.name}")

# 定義狀態推進階段 (總計 4 秒鐘)
# 0~1000ms: Mode=0, CW=0x0080 (復位故障)
# 1000~2000ms: Mode=0, CW=0x0006 (Shutdown -> 期待進入 0x1221)
# 2000~3000ms: Mode=0, CW=0x0007 (Switch On -> 期待進入 0x1223)
# 3000~4000ms: Mode=0, CW=0x000F (Enable -> 期待進入 0x1227)
target_cycle_sec = 0.001
last_sw = None

for i in range(4000):
    t0 = time.perf_counter()

    if i < 1000:
        cw = 0x0080
        stage = "Fault Reset (0x80)"
    elif i < 2000:
        cw = 0x0006
        stage = "Shutdown (0x06)"
    elif i < 3000:
        cw = 0x0007
        stage = "Switch On (0x07)"
    else:
        cw = 0x000F
        stage = "Enable Operation (0x0F)"

    # 送出 RxPDO (CW, TargetPos=0, TargetVel=0, OpMode=0)
    slave.output = PdoHandler.pack_rx(cw, 0, 0, 0)
    master.send_processdata()
    wkc = master.receive_processdata(2000)

    if wkc > 0:
        tx = PdoHandler.unpack_tx(slave.input)
        sw = tx['statusword']
        act_pos = tx['actual_position']

        # 狀態字有跳變時立即印出
        if sw != last_sw or (i % 500 == 0):
            print(f"[{i:4d}ms] {stage:<25} -> SW: {hex(sw)} | 實際位置: {act_pos}")
            last_sw = sw

    elapsed = time.perf_counter() - t0
    sleep_t = target_cycle_sec - elapsed
    if sleep_t > 0:
        time.sleep(sleep_t)

slave.output = PdoHandler.pack_rx(0, 0, 0, 0)
master.send_processdata()
time.sleep(0.01)
master.close()
print(f"[*] 測試結束，最終 Statusword: {hex(last_sw) if last_sw else '無'}")
