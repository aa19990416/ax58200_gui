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

# 1. 啟動 1ms DC
cycle_ns = 1_000_000
try:
    slave.dc_sync(True, cycle_ns)
    master.config_dc()
except Exception:
    pass

master.config_map()
master.state_check(pysoem.SAFEOP_STATE, 50000)

slave.state = pysoem.OP_STATE
master.write_state()
master.state_check(pysoem.OP_STATE, 50000)

if slave.state != pysoem.OP_STATE:
    print(f"[-] 無法進入 OP: {hex(slave.state)}")
    master.close()
    exit()

print("[+] 從站進入 OP 狀態，開始使能序列...")

def pack_rx(cw, pos, vel, mode):
    return struct.pack("<HiiBxxx", cw, pos, vel, mode)

def unpack_tx(data):
    if len(data) >= 11:
        return struct.unpack("<HiiB", data[:11])
    return 0, 0, 0, 0

# 測試階段定義
stages = [
    (0x0000, 200, "1. 歸零 CW (確保 PreControlword=0)"),
    (0x0080, 300, "2. Fault Reset (上升沿復位)"),
    (0x0000, 100, "3. 釋放復位旗標"),
    (0x0006, 500, "4. Shutdown (切換至 Ready -> 預期 0x1221 / 0x1240)"),
    (0x0007, 500, "5. Switch On (上電就緒 -> 預期 0x1223)"),
    (0x000F, 1500, "6. Enable Operation (激磁鎖軸 -> 預期 0x1227)"),
]

target_cycle_sec = 0.001
last_sw = None

for cw, duration_ms, desc in stages:
    print(f"\n[*] 進入階段: {desc} (下發 CW=0x{cw:04X})")
    for i in range(duration_ms):
        t0 = time.perf_counter()

        wkc = master.receive_processdata(2000)
        sw, cur_pos, cur_vel, disp_mode = unpack_tx(slave.input) if (wkc > 0 and slave.input) else (0, 0, 0, 0)

        if sw != last_sw or (i % 250 == 0):
            print(f"    [{i:4d}ms] SW: 0x{sw:04X} | 實際位置: {cur_pos:6d} | 運行模式: {disp_mode}")
            last_sw = sw

        # 目標位置緊隨當前位置
        slave.output = pack_rx(cw, cur_pos, 0, 8)
        master.send_processdata()

        elapsed = time.perf_counter() - t0
        sleep_t = target_cycle_sec - elapsed
        if sleep_t > 0:
            time.sleep(sleep_t)

slave.output = bytes(14)
master.send_processdata()
master.close()
print("\n[*] 測試完成，最終 SW: 0x{:04X}".format(last_sw if last_sw else 0))
