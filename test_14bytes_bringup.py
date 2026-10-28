import pysoem
import time
import struct
import threading

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到從站！請檢查網路連線。")
    exit()

slave = master.slaves[0]
print(f"[+] 找到從站: {slave.name}")

# 1. 依照手冊 Figure 5-46: 啟用 DC 同步 (SYNC0 = 1ms, SYNC1 = 1ms)
cycle_time_ns = 1_000_000
try:
    slave.dc_sync(True, cycle_time_ns, cycle_time_ns)
    master.config_dc()
    print("[+] 已配置 DC 模式 (SYNC0/SYNC1 = 1ms)")
except Exception as e:
    print(f"[!] DC 配置提示: {e}")

# 2. 自動映射 (長度確認為 14 Bytes)
master.config_map()

# 定義 14 Bytes 打包方案
# 方案 A: 尾部填充 3 Bytes (<Hiib3x)
def pack_plan_a(cw, pos=0, vel=0, mode=8):
    return struct.pack("<Hiib3x", cw, pos, vel, mode)

# 方案 B: 內部對齊填充 (<HxxiiBx)
def pack_plan_b(cw, pos=0, vel=0, mode=8):
    return struct.pack("<HxxiiBx", cw, pos, vel, mode)

# 14 Bytes 解包函式
def unpack_plan_a(data):
    if len(data) >= 14:
        return struct.unpack("<Hiib3x", data[:14])
    return 0, 0, 0, 0

def unpack_plan_b(data):
    if len(data) >= 14:
        cw, pos, vel, mode = struct.unpack("<HxxiiBx", data[:14])
        return cw, pos, vel, mode
    return 0, 0, 0, 0

# 全局共享變數
current_cw = 0x0000
current_pack_func = pack_plan_a
last_sw = 0
last_pos = 0
running = True

# 3. 獨立 1ms 背景 PDO 週期通訊執行緒
def pdo_cycle():
    global last_sw, last_pos, running
    while running:
        t0 = time.perf_counter()
        
        # 發送 14 Bytes 封包
        slave.output = current_pack_func(current_cw, last_pos, 0, 8)
        master.send_processdata()
        wkc = master.receive_processdata(2000)

        if wkc > 0 and slave.input:
            sw_a, pos_a, _, _ = unpack_plan_a(slave.input)
            sw_b, pos_b, _, _ = unpack_plan_b(slave.input)
            
            # 自動判定非零狀態字
            if sw_a != 0:
                last_sw = sw_a
                last_pos = pos_a
            elif sw_b != 0:
                last_sw = sw_b
                last_pos = pos_b

        elapsed = time.perf_counter() - t0
        delay = 0.001 - elapsed
        if delay > 0:
            time.sleep(delay)

th = threading.Thread(target=pdo_cycle, daemon=True)
th.start()

# 4. 推進至 SAFEOP 狀態並進行預熱
print("[*] 推進至 SAFEOP 狀態...")
slave.state = pysoem.SAFEOP_STATE
master.write_state()
master.state_check(pysoem.SAFEOP_STATE, 50000)
print("[+] 已進入 SAFEOP，維持 Process Data 封包預熱 1.5 秒...")
time.sleep(1.5)

# 5. 推進至 OP 狀態
print("[*] 推進至 OP 狀態...")
slave.state = pysoem.OP_STATE
master.write_state()
master.state_check(pysoem.OP_STATE, 50000)
print(f"[+] 從站成功進入狀態: {hex(slave.state)} (0x8 代表 OP)")

def run_sequence(plan_name):
    global current_cw
    sequence = [
        (0x0080, 0.4, "Fault Reset (0x0080)"),
        (0x0000, 0.2, "復位清零 (0x0000)"),
        (0x0006, 0.5, "Shutdown (0x0006 -> 期待 0x1221 / 0x1240)"),
        (0x0007, 0.5, "Switch On (0x0007 -> 期待 0x1223)"),
        (0x000F, 1.5, "Enable Operation (0x000F -> 期待 0x1227)"),
    ]
    print(f"\n================ 開始測試: {plan_name} ================")
    for cw, dur, desc in sequence:
        current_cw = cw
        t_end = time.time() + dur
        while time.time() < t_end:
            raw_hex = slave.input.hex() if slave.input else "None"
            print(f"  SW: 0x{last_sw:04X} | Raw: {raw_hex[:16]}... | 步驟: {desc}", end="\r")
            time.sleep(0.1)
        print()
        if last_sw in [0x1221, 0x1240, 0x1223, 0x1227]:
            print(f"\n[+] 狀態機成功跳變！SW: 0x{last_sw:04X}")
            return True
    return False

# 測試方案 A
current_pack_func = pack_plan_a
success = run_sequence("方案 A (尾部填充 3 Bytes: <Hiib3x)")

# 若方案 A 未跳變，測試方案 B
if not success:
    current_pack_func = pack_plan_b
    run_sequence("方案 B (內部對齊填充: <HxxiiBx)")

running = False
th.join()
slave.output = bytes(len(slave.output))
master.send_processdata()
master.close()
print(f"\n[*] 測試流程結束，最終 SW: 0x{last_sw:04X}")
