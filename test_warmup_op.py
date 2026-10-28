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

def pack_rx(cw, pos, vel, mode):
    return struct.pack("<HiiBxxx", cw, pos, vel, mode)

def unpack_tx(data):
    if len(data) >= 11:
        return struct.unpack("<HiiB", data[:11])
    return 0, 0, 0, 0

# 1. 自動映射
master.config_map()

# 2. 推進到 SAFEOP
slave.state = pysoem.SAFEOP_STATE
master.write_state()
master.state_check(pysoem.SAFEOP_STATE, 50000)
print(f"[+] 目前從站狀態 (期待 SAFEOP=4): {hex(slave.state)}")

# 3. 【關鍵預熱】：在 SAFEOP 狀態下持續發送 1 秒鐘的過程數據
# 讓韌體內的 AL_ControlRes() 與 StartInputHandler 跑完，把 bEcatInputUpdateRunning 拉為 TRUE
print("[*] 正在 SAFEOP 狀態下進行時鐘與製程資料預熱 (1000ms)...")
for _ in range(1000):
    t0 = time.perf_counter()
    slave.output = pack_rx(0, 0, 0, 8)
    master.send_processdata()
    master.receive_processdata(2000)
    sleep_t = 0.001 - (time.perf_counter() - t0)
    if sleep_t > 0:
        time.sleep(sleep_t)

# 4. 現在再推進到 OP
slave.state = pysoem.OP_STATE
master.write_state()
master.state_check(pysoem.OP_STATE, 50000)
print(f"[+] 推進後從站狀態 (期待 OP=8): {hex(slave.state)}")

# 5. 開始推進使能序列
stages = [
    (0x0000, 200, "1. 歸零 CW (下降沿)"),
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
