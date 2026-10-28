import pysoem
import struct
import time
import threading

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到從站！請檢查網路介面與接線。")
    exit()

slave = master.slaves[0]
print(f"[+] 找到從站: {slave.name}")

# 配置 PDO
master.config_map()

# 打包 14-byte 封包 (SM-Synchron, CW=0x0006)
def pack_rxpdo(cw=0x0006, pos=0, vel=0, mode=8):
    return struct.pack("<Hiib3x", cw, pos, vel, mode)

running = True
last_wkc = 0

def pdo_cycle():
    global last_wkc, running
    while running:
        t0 = time.perf_counter()
        slave.output = pack_rxpdo(0x0006, 0, 0, 8)
        master.send_processdata()
        wkc = master.receive_processdata(2000)
        last_wkc = wkc
        elapsed = time.perf_counter() - t0
        delay = 0.001 - elapsed
        if delay > 0:
            time.sleep(delay)

th = threading.Thread(target=pdo_cycle, daemon=True)
th.start()

def read_esc_u16(addr):
    try:
        raw = slave._fprd(addr, 2)
        return struct.unpack("<H", raw)[0]
    except Exception:
        return None

# 1. 推進至 SAFEOP 狀態
print("[*] 推進至 SAFEOP 狀態...")
slave.state = pysoem.SAFEOP_STATE
master.write_state()
master.state_check(pysoem.SAFEOP_STATE, 50000)
time.sleep(1.0)

master.read_state()
safeop_al_stat = read_esc_u16(0x0130)
print(f"[+] SAFEOP 階段確認: master.read_state()->slave.state = 0x{slave.state:02X} | ESC 0x0130 = 0x{safeop_al_stat:04X}")

# 2. 請求推進至 OP 狀態
print("\n[*] 發起 OP 請求 (slave.state = OP_STATE -> master.write_state())...")
slave.state = pysoem.OP_STATE
master.write_state()

print("\n" + "=" * 80)
print(f"{'時間':<8} | {'Master.read_state()':<20} | {'0x0120(AL Ctrl)':<16} | {'0x0130(AL Stat)':<16} | {'0x0134(AL Code)':<16} | {'WKC':<5}")
print("=" * 80)

t_start = time.time()
sample_idx = 1

while time.time() - t_start < 3.0:
    # 每次取樣前強制由 ESC 刷新 Master 內部快取
    master.read_state()
    master_seen_state = slave.state
    
    al_ctrl = read_esc_u16(0x0120)
    al_stat = read_esc_u16(0x0130)
    al_code = read_esc_u16(0x0134)
    
    t_rel = f"{time.time() - t_start:.2f}s"
    print(f"#{sample_idx:02d} {t_rel:<5} | slave.state: 0x{master_seen_state:02X}    | 0x{al_ctrl:04X}           | 0x{al_stat:04X}           | 0x{al_code:04X}           | {last_wkc:<5}")
    sample_idx += 1
    time.sleep(0.2)

running = False
th.join()
slave.output = bytes(len(slave.output))
master.send_processdata()
master.close()
print("\n[*] 監控完畢。")
