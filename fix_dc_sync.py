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

# 1. 配置 1ms DC 分散式時鐘 (ESC 暫存器 0x09A0)
cycle_ns = 1_000_000
try:
    slave.dc_sync(True, cycle_ns)
    master.config_dc()
    print("[+] ESC 硬體 DC SYNC0 脈衝已啟用 (1ms)")
except Exception as e:
    print(f"[!] DC 配置提示: {e}")

# 2. 自動映射 PDO
master.config_map()

# 3. 推進至 PREOP / SAFEOP
master.state_check(pysoem.SAFEOP_STATE, 50000)

# 4. 【關鍵修復】僅寫入 Subindex 1 = 2 (啟用 DC 同步模式)，不寫唯讀的 Subindex 2
try:
    slave.sdo_write(0x1C32, 1, struct.pack("<H", 2))
    slave.sdo_write(0x1C33, 1, struct.pack("<H", 2))
    print("[+] 成功將 0x1C32:01 / 0x1C33:01 設為 2 (DC Sync0 模式)")
except Exception as e:
    print(f"[!] 寫入同步模式失敗: {e}")

# 讀取確認
try:
    sm2_sync = struct.unpack("<H", slave.sdo_read(0x1C32, 1))[0]
    sm3_sync = struct.unpack("<H", slave.sdo_read(0x1C33, 1))[0]
    print(f"[*] 驗證目前同步模式: SM2={sm2_sync}, SM3={sm3_sync} (2 代表 DC 成功)")
except Exception as e:
    print(f"[!] 讀取同步模式失敗: {e}")

# 5. 推進到 OP 狀態
slave.state = pysoem.OP_STATE
master.write_state()
master.state_check(pysoem.OP_STATE, 50000)

if slave.state != pysoem.OP_STATE:
    print(f"[-] 無法進入 OP 狀態，目前狀態: {hex(slave.state)}")
    master.close()
    exit()

print("[+] 從站成功進入 OP 狀態！開始發送狀態機推進序列...")

def pack_rx(cw, pos, vel, mode):
    return struct.pack("<HiiBxxx", cw, pos, vel, mode)

def unpack_tx(data):
    if len(data) >= 11:
        return struct.unpack("<HiiB", data[:11])
    return 0, 0, 0, 0

# 執行狀態機推進序列
sequence = [
    (0x0080, "Fault Reset 脈衝"),
    (0x0006, "Shutdown (目標: 0x1221 / 0x1240)"),
    (0x0007, "Switch On (目標: 0x1223)"),
    (0x000F, "Enable Operation (目標: 0x1227)"),
]

for cw, desc in sequence:
    print(f"\n[*] 正在執行: {desc} (下發 CW=0x{cw:04X})")
    for i in range(500):  # 每個階段發送 500ms
        t0 = time.perf_counter()
        
        cur_pos = unpack_tx(slave.input)[1] if slave.input else 0

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
