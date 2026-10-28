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

# 從底層 C 結構取得真實分配的 Configured Address
real_cfg_adr = slave._slave.configadr
print(f"[+] 從站底層真實 Configured Address: 0x{real_cfg_adr:04X}")

# 透過底層 CFFI 直接調用 SOEM 的 ecx_FPRD
def esc_fprd(reg_addr, length, timeout=5000):
    buf = pysoem.pysoem._ffi.new(f"uint8_t[{length}]")
    wkc = pysoem.pysoem._lib.ecx_FPRD(
        master._context,
        real_cfg_adr,
        reg_addr,
        length,
        buf,
        timeout
    )
    if wkc > 0:
        return bytes(pysoem.pysoem._ffi.buffer(buf, length))
    
    # 備份通道: 若 FPRD 未達標，改用 APRD (Auto-Increment Read, 第一個從站地址為 0)
    wkc_aprd = pysoem.pysoem._lib.ecx_APRD(
        master._context,
        0x0000,
        reg_addr,
        length,
        buf,
        timeout
    )
    if wkc_aprd > 0:
        return bytes(pysoem.pysoem._ffi.buffer(buf, length))
    
    return None

def read_u8(addr):
    b = esc_fprd(addr, 1)
    return struct.unpack("<B", b)[0] if b else None

def read_u16(addr):
    b = esc_fprd(addr, 2)
    return struct.unpack("<H", b)[0] if b else None

# 自動配置 PDO
master.config_map()

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

# 推進至 OP 狀態
slave.state = pysoem.SAFEOP_STATE
master.write_state()
master.state_check(pysoem.SAFEOP_STATE, 50000)
time.sleep(0.5)

slave.state = pysoem.OP_STATE
master.write_state()
master.state_check(pysoem.OP_STATE, 50000)
print(f"[+] 從站進入狀態: {hex(slave.state)} (0x8 代表 OP)")
time.sleep(0.5)

print("\n" + "=" * 70)
print("[*] 正在透過底層 FPRD/APRD 讀取 ESC 核心暫存器:")
print("=" * 70)

# 讀取 0x0130, 0x0134, 0x0204, 0x0220
al_status      = read_u16(0x0130)
al_status_code = read_u16(0x0134)
al_event_mask  = read_u16(0x0204)
al_event_req   = read_u16(0x0220)

print(f"1. AL 狀態暫存器:")
print(f"   0x0130 AL Status        : {f'0x{al_status:04X}' if al_status is not None else 'Fail'}")
print(f"   0x0134 AL Status Code   : {f'0x{al_status_code:04X}' if al_status_code is not None else 'Fail'}")

print(f"\n2. PDI 中斷事件暫存器:")
print(f"   0x0204 AL Event Mask    : {f'0x{al_event_mask:04X}' if al_event_mask is not None else 'Fail'}")
print(f"   0x0220 AL Event Request : {f'0x{al_event_req:04X}' if al_event_req is not None else 'Fail'}")

if al_event_mask is not None and al_event_req is not None:
    sm2_in_req  = bool(al_event_req & 0x0100)
    sm2_in_mask = bool(al_event_mask & 0x0100)
    print(f"   >> SM2 Event (Bit 8) in AL Event Request : {sm2_in_req}")
    print(f"   >> SM2 Event (Bit 8) in AL Event Mask    : {sm2_in_mask}")

print(f"\n3. SM2 (Outputs) 物理暫存器 (0x0810 ~ 0x0817):")
sm2_addr = read_u16(0x0810)
sm2_len  = read_u16(0x0812)
sm2_ctrl = read_u8(0x0814)
sm2_stat = read_u8(0x0815)
sm2_act  = read_u8(0x0816)
sm2_pdi  = read_u8(0x0817)

print(f"   0x0810 Physical Start Address : {f'0x{sm2_addr:04X}' if sm2_addr is not None else 'Fail'}")
print(f"   0x0812 Length                 : {sm2_len} Bytes")
print(f"   0x0814 Control Register       : {f'0x{sm2_ctrl:02X}' if sm2_ctrl is not None else 'Fail'}")
print(f"   0x0815 Status Register        : {f'0x{sm2_stat:02X}' if sm2_stat is not None else 'Fail'}")
print(f"   0x0816 Activate Register      : {f'0x{sm2_act:02X}' if sm2_act is not None else 'Fail'}")
print(f"   0x0817 PDI Control Register   : {f'0x{sm2_pdi:02X}' if sm2_pdi is not None else 'Fail'}")
print(f"   >> 通訊 WKC = {last_wkc}")

running = False
th.join()
slave.output = bytes(len(slave.output))
master.send_processdata()
master.close()
print("\n[*] 讀取完成。")
