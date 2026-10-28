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

# 【關鍵】：不啟用 DC 同步！不呼叫 slave.dc_sync() 與 master.config_dc()！
# 讓 ESC 保持在標準 SM 同步模式，讓 MainLoop 能夠直接呼叫 ECAT_Application()

master.config_map()
master.state_check(pysoem.SAFEOP_STATE, 50000)

slave.state = pysoem.OP_STATE
master.write_state()
master.state_check(pysoem.OP_STATE, 50000)

if slave.state != pysoem.OP_STATE:
    print(f"[-] 無法進入 OP 狀態: {hex(slave.state)}")
    master.close()
    exit()

print("[+] 從站成功進入 OP 狀態 (SM 同步模式)！開始下發狀態機推進序列...")

def pack_rx(cw, pos, vel, mode):
    return struct.pack("<HiiBxxx", cw, pos, vel, mode)

def unpack_tx(data):
    if len(data) >= 11:
        return struct.unpack("<HiiB", data[:11])
    return 0, 0, 0, 0

sequence = [
    (0x0080, 500, "1. Fault Reset (復位)"),
    (0x0006, 500, "2. Shutdown (進入準備 -> 0x1221 / 0x1240)"),
    (0x0007, 500, "3. Switch On (上電就緒 -> 0x1223)"),
    (0x000F, 1000, "4. Enable Operation (激磁使能 -> 0x1227)"),
]

target_cycle_sec = 0.001
last_sw = None

for cw, duration_ms, desc in sequence:
    print(f"\n[*] 執行階段: {desc} (下發 CW=0x{cw:04X}, Mode=8)")
    for i in range(duration_ms):
        t0 = time.perf_counter()

        wkc = master.receive_processdata(2000)
        sw, cur_pos, cur_vel, disp_mode = unpack_tx(slave.input) if (wkc > 0 and slave.input) else (0, 0, 0, 0)

        if sw != last_sw or (i % 250 == 0):
            print(f"    [{i:4d}ms] SW: 0x{sw:04X} | 實際位置: {cur_pos:6d} | 運行模式: {disp_mode}")
            last_sw = sw

        # RxPDO 目標位置緊跟當前實際位置
        slave.output = pack_rx(cw, cur_pos, 0, 8)
        master.send_processdata()

        elapsed = time.perf_counter() - t0
        sleep_t = target_cycle_sec - elapsed
        if sleep_t > 0:
            time.sleep(sleep_t)

slave.output = bytes(14)
master.send_processdata()
master.close()
print("\n[*] 流程結束，最終 SW: 0x{:04X}".format(last_sw if last_sw else 0))
