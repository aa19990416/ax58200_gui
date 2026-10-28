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

# 獲取 Configured Address
cfg_adr = None
for attr in ['config_adr', 'config_address', 'configured_address']:
    if hasattr(slave, attr):
        cfg_adr = getattr(slave, attr)
        break

if cfg_adr is None:
    # 若屬性隱藏，從 SOEM context 取得預設 station address (通常為 0x1001)
    cfg_adr = 0x1001
print(f"[+] 從站 Configured Address: 0x{cfg_adr:04X}")

# 1. 設置底層 FPRD (Configured Address Physical Read) 存取通道
def raw_fprd(address, length, timeout=5000):
    # 策略 1: master.fprdb (針對單 byte)
    if length == 1 and hasattr(master, 'fprdb'):
        try:
            val = master.fprdb(cfg_adr, address, timeout)
            return bytes([val])
        except Exception:
            pass
            
    # 策略 2: 直接呼叫 pysoem 底層 CFFI 的 ecx_FPRD
    try:
        cdata_buf = pysoem.pysoem._ffi.new(f"uint8_t[{length}]")
        ret = pysoem.pysoem._lib.ecx_FPRD(
            master._context,
            cfg_adr,
            address,
            length,
            cdata_buf,
            timeout
        )
        if ret > 0:
            return bytes(pysoem.pysoem._ffi.buffer(cdata_buf, length))
    except Exception:
        pass

    # 策略 3: 使用 slave.reg_read (若存在)
    for method in ['reg_read', 'read_reg']:
        if hasattr(slave, method):
            try:
                res = getattr(slave, method)(address, length)
                if res:
                    return res
            except Exception:
                pass

    return None

def read_esc_u8(address):
    b = raw_fprd(address, 1)
    return struct.unpack("<B", b)[0] if b and len(b) >= 1 else None

def read_esc_u16(address):
    b = raw_fprd(address, 2)
    return struct.unpack("<H", b)[0] if b and len(b) >= 2 else None

# 2. 自動配置 PDO (SM-Synchron, 14 Bytes)
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

# 3. 推進至 OP 狀態
print("[*] 推進至 SAFEOP 狀態...")
slave.state = pysoem.SAFEOP_STATE
master.write_state()
master.state_check(pysoem.SAFEOP_STATE, 50000)
time.sleep(0.5)

print("[*] 推進至 OP 狀態...")
slave.state = pysoem.OP_STATE
master.write_state()
master.state_check(pysoem.OP_STATE, 50000)
print(f"[+] 從站進入狀態: {hex(slave.state)} (0x8 代表 OP)")

time.sleep(0.5)

print("\n" + "=" * 70)
print("[*] 在 OP 狀態且 1ms PDO (CW=0x0006) 持續發送下讀取 ESC 暫存器:")
print("=" * 70)

# 第一優先讀取：0x0130, 0x0134, 0x0204, 0x0220
al_status      = read_esc_u16(0x0130)
al_status_code = read_esc_u16(0x0134)
al_event_mask  = read_esc_u16(0x0204)
al_event_req   = read_esc_u16(0x0220)

print(f"1. AL 狀態暫存器:")
print(f"   0x0130 AL Status       : {f'0x{al_status:04X}' if al_status is not None else '讀取失敗'}")
print(f"   0x0134 AL Status Code  : {f'0x{al_status_code:04X}' if al_status_code is not None else '讀取失敗'}")

print(f"\n2. PDI 中斷事件暫存器 (關鍵比對):")
print(f"   0x0204 AL Event Mask   : {f'0x{al_event_mask:04X}' if al_event_mask is not None else '讀取失敗'}")
print(f"   0x0220 AL Event Request: {f'0x{al_event_req:04X}' if al_event_req is not None else '讀取失敗'}")

# 解析 PROCESS_OUTPUT_EVENT (Bit 8 = 0x0100)
if al_event_mask is not None and al_event_req is not None:
    sm2_event_in_req  = bool(al_event_req & 0x0100)
    sm2_event_in_mask = bool(al_event_mask & 0x0100)
    print(f"   >> SM2 Event (Bit 8) in AL Event Request: {sm2_event_in_req}")
    print(f"   >> SM2 Event (Bit 8) in AL Event Mask   : {sm2_event_in_mask}")

print(f"\n3. SM2 (Outputs) 暫存器組 (0x0810 ~ 0x0817):")
sm2_addr = read_esc_u16(0x0810)
sm2_len  = read_esc_u16(0x0812)
sm2_ctrl = read_esc_u8(0x0814)
sm2_stat = read_esc_u8(0x0815)
sm2_act  = read_esc_u8(0x0816)
sm2_pdi  = read_esc_u8(0x0817)

print(f"   0x0810 Physical Start Address : {f'0x{sm2_addr:04X}' if sm2_addr is not None else 'Fail'}")
print(f"   0x0812 Length                 : {sm2_len} Bytes (預期 14 Bytes)")
print(f"   0x0814 Control Register       : {f'0x{sm2_ctrl:02X}' if sm2_ctrl is not None else 'Fail'}")
print(f"   0x0815 Status Register        : {f'0x{sm2_stat:02X}' if sm2_stat is not None else 'Fail'}")
print(f"   0x0816 Activate Register      : {f'0x{sm2_act:02X}' if sm2_act is not None else 'Fail'}")
print(f"   0x0817 PDI Control Register   : {f'0x{sm2_pdi:02X}' if sm2_pdi is not None else 'Fail'}")
print(f"   >> 當前即時通訊 WKC = {last_wkc}")

running = False
th.join()
slave.output = bytes(len(slave.output))
master.send_processdata()
master.close()
print("\n[*] 讀取完成。")
