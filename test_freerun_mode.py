import pysoem
import time
import struct

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到從站！")
    exit()

slave = master.slaves[0]
print(f"[+] 連接從站: {slave.name}")

# 1. 確保從站在 PREOP
slave.state = pysoem.PREOP_STATE
master.write_state()
master.state_check(pysoem.PREOP_STATE, 50000)

# 2. 將 SM2 (0x1C32:01) 與 SM3 (0x1C33:01) 設為 0 (FreeRun 模式)
try:
    slave.sdo_write(0x1C32, 1, struct.pack("<H", 0))
    slave.sdo_write(0x1C33, 1, struct.pack("<H", 0))
    print("[+] 成功將同步模式設置為 FreeRun (0x1C32:01 = 0, 0x1C33:01 = 0)")
except Exception as e:
    print(f"[!] 寫入 FreeRun 模式提示: {e}")

# 3. 映射 PDO
master.config_map()

# 4. 推進到 SAFEOP
slave.state = pysoem.SAFEOP_STATE
master.write_state()
master.state_check(pysoem.SAFEOP_STATE, 50000)
print(f"[+] 推進至 SAFEOP: {hex(slave.state)}")

# 5. 打包函式
def pack_rx(cw, pos, vel, mode):
    return struct.pack("<HiiBxxx", cw, pos, vel, mode)

def unpack_tx(data):
    if len(data) >= 11:
        return struct.unpack("<HiiB", data[:11])
    return 0, 0, 0, 0

# 在 SAFEOP 先送幾包
for _ in range(50):
    slave.output = pack_rx(0, 0, 0, 8)
    master.send_processdata()
    master.receive_processdata(2000)
    time.sleep(0.002)

# 6. 推進至 OP
slave.state = pysoem.OP_STATE
master.write_state()
master.state_check(pysoem.OP_STATE, 50000)
print(f"[+] 推進至 OP: {hex(slave.state)}")

# 7. 連續下發使能序列
sequence = [
    (0x0080, 200, "Fault Reset (0x80)"),
    (0x0006, 300, "Shutdown (0x06 -> 預期 0x1221/0x1240)"),
    (0x0007, 300, "Switch On (0x07 -> 預期 0x1223)"),
    (0x000F, 500, "Enable Operation (0x0F -> 預期 0x1227)"),
]

for cw, duration_ms, desc in sequence:
    print(f"\n[*] 執行: {desc}")
    for i in range(duration_ms):
        t0 = time.perf_counter()

        slave.output = pack_rx(cw, 0, 0, 8)
        master.send_processdata()
        wkc = master.receive_processdata(2000)

        if wkc > 0 and slave.input:
            sw, pos, vel, mode = unpack_tx(slave.input)
            if i % 100 == 0:
                print(f"    [{i:3d}ms] SW: 0x{sw:04X} | Pos: {pos} | Mode: {mode}")

        sleep_t = 0.001 - (time.perf_counter() - t0)
        if sleep_t > 0:
            time.sleep(sleep_t)

master.close()
print("\n[*] 結束")
