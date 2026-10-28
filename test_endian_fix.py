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

# 嚴格 14 Bytes 打包
def pack_rx_swapped(cw, pos, vel, mode):
    # 將 cw 進行 16-bit Byte Swap (例如 0x0006 -> 0x0600)
    cw_swapped = ((cw & 0xFF) << 8) | ((cw >> 8) & 0xFF)
    return struct.pack("<HiiBxxx", cw_swapped, pos, vel, mode)

def unpack_tx(data):
    if len(data) >= 11:
        return struct.unpack("<HiiB", data[:11])
    return 0, 0, 0, 0

# 1. 映射並推進到 SAFEOP 預熱
master.config_map()
slave.state = pysoem.SAFEOP_STATE
master.write_state()
master.state_check(pysoem.SAFEOP_STATE, 50000)

print("[*] SAFEOP 預熱中...")
for _ in range(500):
    t0 = time.perf_counter()
    slave.output = pack_rx_swapped(0, 0, 0, 8)
    master.send_processdata()
    master.receive_processdata(2000)
    sleep_t = 0.001 - (time.perf_counter() - t0)
    if sleep_t > 0:
        time.sleep(sleep_t)

# 2. 推進到 OP
slave.state = pysoem.OP_STATE
master.write_state()
master.state_check(pysoem.OP_STATE, 50000)
print(f"[+] 從站進入 OP 狀態: {hex(slave.state)}")

# 3. 發送預先 Byte Swap 的控制序列
stages = [
    (0x0000, 200, "1. 歸零 CW"),
    (0x0080, 300, "2. Fault Reset (發送 0x8000)"),
    (0x0000, 100, "3. 釋放復位旗標"),
    (0x0006, 500, "4. Shutdown (發送 0x0600 -> 期待 0x1221 / 0x1240)"),
    (0x0007, 500, "5. Switch On (發送 0x0700 -> 期待 0x1223)"),
    (0x000F, 1500, "6. Enable Operation (發送 0x0F00 -> 期待 0x1227)"),
]

target_cycle_sec = 0.001
last_sw = None

for cw, duration_ms, desc in stages:
    print(f"\n[*] 進入階段: {desc}")
    for i in range(duration_ms):
        t0 = time.perf_counter()

        wkc = master.receive_processdata(2000)
        sw, cur_pos, cur_vel, disp_mode = unpack_tx(slave.input) if (wkc > 0 and slave.input) else (0, 0, 0, 0)

        if sw != last_sw or (i % 250 == 0):
            print(f"    [{i:4d}ms] SW: 0x{sw:04X} | 實際位置: {cur_pos:6d} | 運行模式: {disp_mode}")
            last_sw = sw

        slave.output = pack_rx_swapped(cw, cur_pos, 0, 8)
        master.send_processdata()

        elapsed = time.perf_counter() - t0
        sleep_t = target_cycle_sec - elapsed
        if sleep_t > 0:
            time.sleep(sleep_t)

slave.output = bytes(14)
master.send_processdata()
master.close()
print("\n[*] 測試完成，最終 SW: 0x{:04X}".format(last_sw if last_sw else 0))
