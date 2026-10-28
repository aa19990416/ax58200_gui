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

# 自動配置 PDO
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

# 推進至 SAFEOP -> OP
slave.state = pysoem.SAFEOP_STATE
master.write_state()
master.state_check(pysoem.SAFEOP_STATE, 50000)
time.sleep(0.5)

slave.state = pysoem.OP_STATE
master.write_state()
master.state_check(pysoem.OP_STATE, 50000)
print(f"[+] 從站進入狀態: {hex(slave.state)} (0x8 代表 OP)")
time.sleep(0.5)

# 使用 slave._fprd 讀取 ESC 暫存器
def read_esc_u16(addr):
    try:
        raw = slave._fprd(addr, 2)
        return struct.unpack("<H", raw)[0]
    except Exception:
        return None

def read_esc_u8(addr):
    try:
        raw = slave._fprd(addr, 1)
        return struct.unpack("<B", raw)[0]
    except Exception:
        return None

print("\n" + "=" * 70)
print("[*] OP 狀態下透過 slave._fprd 讀取 ESC 核心暫存器 (1ms PDO 發送中):")
print("=" * 70)

# 1. 讀取 AL Status 與 AL Status Code
al_status      = read_esc_u16(0x0130)
al_status_code = read_esc_u16(0x0134)
print(f"1. AL 狀態暫存器:")
print(f"   0x0130 AL Status        : {f'0x{al_status:04X}' if al_status is not None else '讀取失敗'}")
print(f"   0x0134 AL Status Code   : {f'0x{al_status_code:04X}' if al_status_code is not None else '讀取失敗'}")

# 2. 讀取 PDI 中斷事件暫存器
al_event_mask = read_esc_u16(0x0204)
al_event_req  = read_esc_u16(0x0220)
print(f"\n2. PDI 中斷事件暫存器 (關鍵比對):")
print(f"   0x0204 AL Event Mask    : {f'0x{al_event_mask:04X}' if al_event_mask is not None else '讀取失敗'}")
print(f"   0x0220 AL Event Request : {f'0x{al_event_req:04X}' if al_event_req is not None else '讀取失敗'}")

if al_event_mask is not None and al_event_req is not None:
    sm2_in_req  = bool(al_event_req & 0x0100)
    sm2_in_mask = bool(al_event_mask & 0x0100)
    print(f"   >> SM2 Event (Bit 8) in AL Event Request : {sm2_in_req} (ESC 是否產生 SM2 接收事件)")
    print(f"   >> SM2 Event (Bit 8) in AL Event Mask    : {sm2_in_mask} (PDI 中斷是否放行該事件給 MCU)")

# 3. 讀取 SM2 (Outputs) 物理暫存器
sm2_addr = read_esc_u16(0x0810)
sm2_len  = read_esc_u16(0x0812)
sm2_ctrl = read_esc_u8(0x0814)
sm2_stat = read_esc_u8(0x0815)
sm2_act  = read_esc_u8(0x0816)
sm2_pdi  = read_esc_u8(0x0817)

print(f"\n3. SM2 (Outputs) 物理暫存器 (0x0810 ~ 0x0817):")
print(f"   0x0810 Physical Start Address : {f'0x{sm2_addr:04X}' if sm2_addr is not None else 'Fail'}")
print(f"   0x0812 Length                 : {sm2_len} Bytes")
print(f"   0x0814 Control Register       : {f'0x{sm2_ctrl:02X}' if sm2_ctrl is not None else 'Fail'}")
print(f"   0x0815 Status Register        : {f'0x{sm2_stat:02X}' if sm2_stat is not None else 'Fail'}")
print(f"   0x0816 Activate Register      : {f'0x{sm2_act:02X}' if sm2_act is not None else 'Fail'}")
print(f"   0x0817 PDI Control Register   : {f'0x{sm2_pdi:02X}' if sm2_pdi is not None else 'Fail'}")
print(f"   >> 當前週期通訊 WKC = {last_wkc}")

running = False
th.join()
slave.output = bytes(len(slave.output))
master.send_processdata()
master.close()
print("\n[*] 讀取完成。")
