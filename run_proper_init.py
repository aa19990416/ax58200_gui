import pysoem
import time
import struct

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到從站！")
    exit()

slave = master.slaves[0]
print(f"[+] 找到從站: {slave.name}")

# 1. 在 PREOP 狀態下配置 Sync Manager Assignment (手冊 Table 8-2)
try:
    # 清空並指定 SM2 (0x1C12) -> 0x1600 (RxPDO)
    slave.sdo_write(0x1C12, 0, struct.pack("<B", 0))
    slave.sdo_write(0x1C12, 1, struct.pack("<H", 0x1600))
    slave.sdo_write(0x1C12, 0, struct.pack("<B", 1))

    # 清空並指定 SM3 (0x1C13) -> 0x1A00 (TxPDO)
    slave.sdo_write(0x1C13, 0, struct.pack("<B", 0))
    slave.sdo_write(0x1C13, 1, struct.pack("<H", 0x1A00))
    slave.sdo_write(0x1C13, 0, struct.pack("<B", 1))
    print("[+] 成功寫入 SM2/SM3 PDO 分配物件 (0x1C12 / 0x1C13)")
except Exception as e:
    print(f"[!] SM 分配物件寫入提示: {e}")

# 2. 自動映射 PDO
master.config_map()

# 3. 配置 1ms DC 同步 (手冊 Chapter 9-1: 1ms DC Cycle)
cycle_ns = 1_000_000
try:
    slave.dc_sync(True, cycle_ns)
    master.config_dc()
    print("[+] DC 分散式時鐘同步已配置 (1ms)")
except Exception as e:
    print(f"[!] DC 配置提示: {e}")

# 4. 推進到 SAFE_OP 狀態
master.state_check(pysoem.SAFEOP_STATE, 50000)

# 5. 寫入同步週期至 0x1C32:02 (SM2 Output) 與 0x1C33:02 (SM3 Input)
try:
    slave.sdo_write(0x1C32, 2, struct.pack("<I", cycle_ns))
    slave.sdo_write(0x1C33, 2, struct.pack("<I", cycle_ns))
    # Synchronization Type = 2 (DC Sync0)
    slave.sdo_write(0x1C32, 1, struct.pack("<H", 2))
    slave.sdo_write(0x1C33, 1, struct.pack("<H", 2))
    print("[+] 成功啟用硬體 DC SYNC0 同步模式 (0x1C32/0x1C33 = 2)")
except Exception as e:
    print(f"[!] 同步參數寫入提示: {e}")

# 6. 推進到 OP 狀態
slave.state = pysoem.OP_STATE
master.write_state()
master.state_check(pysoem.OP_STATE, 50000)

if slave.state != pysoem.OP_STATE:
    print(f"[-] 無法進入 OP 狀態，目前狀態: {hex(slave.state)}")
    master.close()
    exit()

print("[+] 從站已成功進入 OP 狀態！開始發送狀態機解鎖脈衝...")

def pack_rx(cw, pos, vel, mode):
    return struct.pack("<HiiBxxx", cw, pos, vel, mode)

def unpack_tx(data):
    if len(data) >= 11:
        return struct.unpack("<HiiB", data[:11])
    return 0, 0, 0, 0

# 執行狀態機推進序列
sequence = [
    (0x0080, "Fault Reset 脈衝"),
    (0x0006, "Shutdown (轉至 Ready 0x1221)"),
    (0x0007, "Switch On (轉至 Switched On 0x1223)"),
    (0x000F, "Enable Operation (轉至 0x1227)"),
]

for cw, desc in sequence:
    print(f"\n[*] 正在執行: {desc} (下發 CW=0x{cw:04X})")
    for i in range(500):  # 每個階段發送 500ms
        t0 = time.perf_counter()
        
        # 讀取當前反饋位置以避免突波
        if slave.input:
            sw_cur, cur_pos, _, _ = unpack_tx(slave.input)
        else:
            cur_pos = 0

        slave.output = pack_rx(cw, cur_pos, 0, 8)
        master.send_processdata()
        wkc = master.receive_processdata(2000)

        if wkc > 0 and i % 100 == 0:
            sw, pos, vel, disp_mode = unpack_tx(slave.input)
            raw_hex = ' '.join(f'{b:02X}' for b in slave.input)
            print(f"    [{i:3d}ms] Raw: {raw_hex} | SW: 0x{sw:04X} | Pos: {pos}")

        elapsed = time.perf_counter() - t0
        sleep_t = 0.001 - elapsed
        if sleep_t > 0:
            time.sleep(sleep_t)

slave.output = bytes(14)
master.send_processdata()
master.close()
print("\n[*] 測試完成。")
