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

# 1. 映射配置
master.config_map()

# 全局變數供背景線程使用
current_cw = 0x0000
current_mode = 8
running = True
thread_lock = threading.Lock()

last_sw = 0
last_pos = 0
last_mode = 0

def pack_rx(cw, pos, vel, mode):
    return struct.pack("<HiiBxxx", cw, pos, vel, mode)

def unpack_tx(data):
    if len(data) >= 11:
        return struct.unpack("<HiiB", data[:11])
    return 0, 0, 0, 0

# 2. 定時製程數據背景線程 (以嚴格 1ms 週期持續發送)
def pdo_cycle_thread():
    global last_sw, last_pos, last_mode, running
    while running:
        t0 = time.perf_counter()
        
        with thread_lock:
            cw = current_cw
            mode = current_mode
            pos = last_pos

        slave.output = pack_rx(cw, pos, 0, mode)
        master.send_processdata()
        wkc = master.receive_processdata(2000)

        if wkc > 0 and slave.input:
            sw, cur_pos, cur_vel, disp_mode = unpack_tx(slave.input)
            with thread_lock:
                last_sw = sw
                last_pos = cur_pos
                last_mode = disp_mode

        elapsed = time.perf_counter() - t0
        sleep_t = 0.001 - elapsed
        if sleep_t > 0:
            time.sleep(sleep_t)

# 3. 啟動背景發送線程（在 PREOP 階段就先開始送，保證緩衝區不空）
io_thread = threading.Thread(target=pdo_cycle_thread, daemon=True)
io_thread.start()

print("[*] 背景 1ms 週期通訊線程已啟動")

# 4. 嚴格逐步推進 ESM: PREOP -> SAFEOP
print("[*] 推進至 SAFEOP...")
slave.state = pysoem.SAFEOP_STATE
master.write_state()
master.state_check(pysoem.SAFEOP_STATE, 50000)
print(f"[+] 成功進入 SAFEOP: {hex(slave.state)}")

# 在 SAFEOP 維持穩定收發 500ms
time.sleep(0.5)

# 5. 推進至 OP
print("[*] 推進至 OP...")
slave.state = pysoem.OP_STATE
master.write_state()
master.state_check(pysoem.OP_STATE, 50000)
print(f"[+] 成功進入 OP 狀態: {hex(slave.state)}")

# 6. CiA 402 狀態機推進序列
sequence = [
    (0x0000, 0.3, "1. 歸零 (CW=0x0000)"),
    (0x0080, 0.3, "2. Fault Reset 復位 (CW=0x0080)"),
    (0x0000, 0.2, "3. 復位清零 (CW=0x0000)"),
    (0x0006, 0.5, "4. Shutdown 進入準備 (CW=0x0006 -> 期待 0x1221 / 0x1240)"),
    (0x0007, 0.5, "5. Switch On 上電就緒 (CW=0x0007 -> 期待 0x1223)"),
    (0x000F, 1.5, "6. Enable Operation 激磁使能 (CW=0x000F -> 期待 0x1227)"),
]

for cw, dur, desc in sequence:
    print(f"\n[*] 執行階段: {desc}")
    with thread_lock:
        current_cw = cw
    
    t_end = time.time() + dur
    while time.time() < t_end:
        with thread_lock:
            sw = last_sw
            pos = last_pos
            m = last_mode
        print(f"    SW: 0x{sw:04X} | 實際位置: {pos:6d} | 模式回報: {m}", end="\r")
        time.sleep(0.1)
    print()

# 停止通訊
running = False
io_thread.join()
slave.output = bytes(14)
master.send_processdata()
master.close()
print("\n[*] 測試完成。")
