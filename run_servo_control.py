import pysoem
import struct
import time
import threading

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到 EtherCAT 從站！")
    exit()

slave = master.slaves[0]
print(f"[+] 找到從站: {slave.name}")

# 在 PREOP 先明確鎖定 Mode 8 (CSP)
slave.state = pysoem.PREOP_STATE
master.write_state()
master.state_check(pysoem.PREOP_STATE, 50000)
time.sleep(0.1)

try:
    slave.sdo_write(0x6060, 0, struct.pack("<b", 8))
except Exception:
    pass

master.config_map()

def pack_rxpdo(cw, pos=0, vel=0, mode=8):
    return struct.pack("<Hiib3x", cw, pos, vel, mode)

def unpack_txpdo(data):
    if len(data) >= 14:
        return struct.unpack("<Hiib3x", data[:14])
    return 0, 0, 0, 0

current_cw = 0x0000
target_pos = 0
target_vel = 0
current_mode = 8

last_sw = 0
actual_pos = 0
actual_vel = 0
actual_mode = 0
last_wkc = 0
running = True
hold_position = True  # 是否保持原位鎖定

def pdo_loop():
    global last_sw, actual_pos, actual_vel, actual_mode, last_wkc, target_pos, running
    while running:
        t0 = time.perf_counter()
        
        # 若未進入主動軌跡控制，目標位置強制跟隨實際位置，避免使能時暴衝
        if hold_position:
            target_pos = actual_pos

        slave.output = pack_rxpdo(current_cw, target_pos, target_vel, current_mode)
        master.send_processdata()
        wkc = master.receive_processdata(2000)
        last_wkc = wkc

        if wkc > 0 and slave.input:
            sw, pos, vel, mode = unpack_txpdo(slave.input)
            last_sw = sw
            actual_pos = pos
            actual_vel = vel
            actual_mode = mode

        elapsed = time.perf_counter() - t0
        delay = 0.001 - elapsed
        if delay > 0:
            time.sleep(delay)

th = threading.Thread(target=pdo_loop, daemon=True)
th.start()

def read_u16(addr):
    try:
        return struct.unpack("<H", slave._fprd(addr, 2))[0]
    except Exception:
        return None

def write_u16(addr, val):
    slave._fpwr(addr, struct.pack("<H", val))

# 1. 推進至 SAFEOP
slave.state = pysoem.SAFEOP_STATE
master.write_state()
master.state_check(pysoem.SAFEOP_STATE, 50000)
time.sleep(0.2)

cur_stat = read_u16(0x0130)
if cur_stat and (cur_stat & 0x0010):
    write_u16(0x0120, 0x0014)
    time.sleep(0.1)
write_u16(0x0120, 0x0004)
time.sleep(0.1)

# 2. 強制進入 OP
write_u16(0x0120, 0x0008)
time.sleep(0.3)
print(f"[+] 從站真實狀態確認: AL Status = 0x{read_u16(0x0130):04X}")

# 3. 狀態判定
def get_cia_state(sw):
    if (sw & 0x004F) == 0x0000: return "NOT_READY_TO_SWITCH_ON"
    elif (sw & 0x004F) == 0x0040: return "SWITCH_ON_DISABLED"
    elif (sw & 0x006F) == 0x0021: return "READY_TO_SWITCH_ON"
    elif (sw & 0x006F) == 0x0023: return "SWITCHED_ON"
    elif (sw & 0x006F) == 0x0027: return "OPERATION_ENABLED"
    elif (sw & 0x004F) == 0x0008: return "FAULT"
    return "UNKNOWN"

time.sleep(0.2)
print(f"[+] 起始反饋: SW = 0x{last_sw:04X} ({get_cia_state(last_sw)}), 位置 = {actual_pos}")

# =====================================================================
# 4. 平滑無衝擊 Servo-On
# =====================================================================
def enable_servo():
    global current_cw
    print("\n[*] 正在執行平滑無衝擊 Servo-On...")
    
    # Fault Reset
    if (last_sw & 0x0008) != 0:
        current_cw = 0x0000
        time.sleep(0.1)
        current_cw = 0x0080
        time.sleep(0.2)
        current_cw = 0x0000
        time.sleep(0.1)

    # 依序切換使能
    for cw, wait_sw, name in [(0x0006, 0x0021, "Ready to Switch On"), 
                              (0x0007, 0x0023, "Switched On"), 
                              (0x000F, 0x0027, "Operation Enabled (激磁鎖軸)")]:
        current_cw = cw
        t_wait = time.time() + 2.0
        while time.time() < t_wait:
            if (last_sw & 0x006F) == wait_sw:
                print(f"[+] 進入 {name} (SW=0x{last_sw:04X})")
                break
            time.sleep(0.02)

    return (last_sw & 0x006F) == 0x0027

# =====================================================================
# 5. Homing 流程 (Mode 6)
# =====================================================================
def run_homing():
    global current_cw, current_mode, hold_position
    hold_position = False  # 交出位置控制權給驅動器本體
    print("\n[*] 開始執行 Homing (回原點模式)...")
    
    current_mode = 6
    time.sleep(0.1)
    current_cw = 0x000F
    time.sleep(0.1)
    
    print("[*] 觸發 Homing 運轉 (CW = 0x001F)...")
    current_cw = 0x001F
    
    t_start = time.time()
    while time.time() - t_start < 6.0:
        is_attained = bool(last_sw & 0x1000)
        is_error = bool(last_sw & 0x2000)
        print(f"    SW: 0x{last_sw:04X} | 位置: {actual_pos:8d} | Homing完成: {is_attained}", end="\r")
        
        if is_attained:
            print(f"\n[+] Homing 成功完成！原點位置: {actual_pos}")
            current_cw = 0x000F
            time.sleep(0.5)
            # 切回 CSP 並重新鎖定當前原點
            current_mode = 8
            hold_position = True
            return True
        time.sleep(0.05)

    print("\n[*] Homing 結束。")
    current_mode = 8
    hold_position = True
    return False

# =====================================================================
# 6. 主控邏輯
# =====================================================================
try:
    if enable_servo():
        print(f"\n[!!! 使能成功 !!!] 馬達已平滑激磁，原位鎖定中 (位置: {actual_pos})。")
        print("（此時你可以試著用手摸摸看，軸應已具備電磁阻力且完全不會亂跳）")
        
        print("\n請選擇下一步操作：")
        print("  1: 執行 Homing (回原點)")
        print("  2: 執行 CSP 微幅正反點動測試 (±1000 counts)")
        print("  Q: 退出")
        
        choice = input("\n請輸入選項 [1/2/Q]: ").strip()
        
        if choice == "1":
            run_homing()
        elif choice == "2":
            print("\n[*] 進入 CSP 模式點動測試...")
            hold_position = False
            base_pos = actual_pos
            
            print("[*] 正轉 1000 counts...")
            for i in range(100):
                target_pos = base_pos + int(1000 * (i / 100.0))
                time.sleep(0.01)
            time.sleep(0.5)
            
            print("[*] 反轉回原位...")
            for i in range(100):
                target_pos = base_pos + 1000 - int(1000 * (i / 100.0))
                time.sleep(0.01)
            
            print(f"[+] 點動測試完成，最終位置: {actual_pos}")
            hold_position = True
            
        print("\n[*] 保持鎖軸 3 秒後安全釋放...")
        time.sleep(3.0)

except KeyboardInterrupt:
    print("\n[*] 使用者中斷。")

finally:
    current_cw = 0x0000
    time.sleep(0.1)
    running = False
    th.join()
    slave.output = bytes(len(slave.output))
    master.send_processdata()
    master.close()
    print("[*] EtherCAT 連線已安全釋放。")
