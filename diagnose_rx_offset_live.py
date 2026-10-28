import pysoem
import time
import struct
import threading

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到從站！")
    exit()

slave = master.slaves[0]
print(f"[+] 找到從站: {slave.name}")

cycle_time_ns = 1_000_000
try:
    slave.dc_sync(True, cycle_time_ns, cycle_time_ns)
    master.config_dc()
except Exception as e:
    pass

master.config_map()

current_rx_bytes = bytearray(len(slave.output))
last_sw = 0
last_pos = 0
running = True

def pdo_loop():
    global last_sw, last_pos, running
    while running:
        t0 = time.perf_counter()
        slave.output = bytes(current_rx_bytes)
        master.send_processdata()
        wkc = master.receive_processdata(2000)
        if wkc > 0 and slave.input and len(slave.input) >= 2:
            last_sw = struct.unpack("<H", slave.input[:2])[0]
            if len(slave.input) >= 6:
                last_pos = struct.unpack("<i", slave.input[2:6])[0]
        elapsed = time.perf_counter() - t0
        delay = 0.001 - elapsed
        if delay > 0:
            time.sleep(delay)

th = threading.Thread(target=pdo_loop, daemon=True)
th.start()

slave.state = pysoem.SAFEOP_STATE
master.write_state()
master.state_check(pysoem.SAFEOP_STATE, 50000)
time.sleep(1.0)

slave.state = pysoem.OP_STATE
master.write_state()
master.state_check(pysoem.OP_STATE, 50000)
print(f"[+] 從站進入 OP 狀態: {hex(slave.state)}")

def read_mcu_cw():
    try:
        raw = slave.sdo_read(0x6040, 0)
        return struct.unpack("<H", raw)[0]
    except Exception:
        return None

print("\n[*] 開始即時探測 RxPDO 的 Controlword 偏移位址...")
found = False

for offset in range(0, len(slave.output) - 1):
    test_buf = bytearray(len(slave.output))
    # 在目標 offset 寫入 0x0006
    struct.pack_into("<H", test_buf, offset, 0x0006)
    
    # 若空間允許，在後續位置填入 CSP 模式 (8)
    if offset + 10 < len(slave.output):
        test_buf[offset + 10] = 8
        
    current_rx_bytes[:] = test_buf
    time.sleep(0.15)
    
    mcu_cw = read_mcu_cw()
    print(f"  測試 Offset {offset:2d} | 發送: {test_buf.hex()} | MCU 0x6040={hex(mcu_cw) if mcu_cw is not None else 'Fail'} | SW={hex(last_sw)}")
    
    if mcu_cw == 0x0006:
        print(f"\n[!!! 命中成功 !!!] MCU 成功收到 0x0006！Controlword 位於 Offset: {offset} Bytes！")
        found = True
        break

if not found:
    print("\n[-] 所有 Offset 均未被 MCU 寫入 0x6040，代表輸出資料未被 APPL_OutputMapping 搬移至物件字典。")

running = False
th.join()
slave.output = bytes(len(slave.output))
master.send_processdata()
master.close()
