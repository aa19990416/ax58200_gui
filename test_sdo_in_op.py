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
print(f"[+] 連接從站: {slave.name}")

# 1. 映射配置
master.config_map()

# 2. 定時製程資料發送線程 (確保維持 OP 通訊)
running = True
def pdo_loop():
    while running:
        t0 = time.perf_counter()
        master.send_processdata()
        master.receive_processdata(2000)
        sleep_t = 0.001 - (time.perf_counter() - t0)
        if sleep_t > 0:
            time.sleep(sleep_t)

th = threading.Thread(target=pdo_loop, daemon=True)
th.start()

# 3. 逐步切入 SAFEOP -> OP
slave.state = pysoem.SAFEOP_STATE
master.write_state()
master.state_check(pysoem.SAFEOP_STATE, 50000)
time.sleep(0.2)

slave.state = pysoem.OP_STATE
master.write_state()
master.state_check(pysoem.OP_STATE, 50000)
print(f"[+] 從站已成功進入狀態: {hex(slave.state)} (0x8 代表 OP)")

def sdo_write_cw(cw_val):
    slave.sdo_write(0x6040, 0, struct.pack("<H", cw_val))

def sdo_read_sw():
    return struct.unpack("<H", slave.sdo_read(0x6041, 0))[0]

time.sleep(0.5)
print(f"[*] 進入 OP 後初始 0x6041 Statusword: 0x{sdo_read_sw():04X}")

# 4. 在 OP 狀態下透過 SDO 推進狀態機
sequence = [
    (0x0000, "1. 歸零 (CW=0x0000)"),
    (0x0080, "2. Fault Reset (CW=0x0080)"),
    (0x0000, "3. 復位清零 (CW=0x0000)"),
    (0x0006, "4. Shutdown (CW=0x0006 -> 期待 0x1221 / 0x1240)"),
    (0x0007, "5. Switch On (CW=0x0007 -> 期待 0x1223)"),
    (0x000F, "6. Enable Operation (CW=0x000F -> 期待 0x1227)"),
]

for cw, desc in sequence:
    print(f"\n[*] 執行: {desc}")
    try:
        sdo_write_cw(cw)
        time.sleep(0.2)
        sw = sdo_read_sw()
        cw_read = struct.unpack("<H", slave.sdo_read(0x6040, 0))[0]
        print(f"    -> 寫入 CW=0x{cw:04X} | 讀出 0x6040=0x{cw_read:04X} | 0x6041 SW=0x{sw:04X}")
    except Exception as e:
        print(f"    [-] SDO 操作失敗: {e}")

running = False
th.join()
master.close()
print("\n[*] 測試結束")
