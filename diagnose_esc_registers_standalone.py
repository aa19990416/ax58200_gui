import pysoem
import struct
import time
import threading
import ctypes

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到從站！請檢查網路介面與接線。")
    exit()

slave = master.slaves[0]
cfg_adr = slave.config_adr
print(f"[+] 找到從站: {slave.name} (Configured Address: 0x{cfg_adr:04X})")

# 尋找系統中的 libsoem.so 進行標準 FPRD 讀取
soem_lib = None
for lib_name in ["libsoem.so", "libsoem.so.1", "/usr/local/lib/libsoem.so", "/usr/lib/libsoem.so"]:
    try:
        soem_lib = ctypes.CDLL(lib_name)
        break
    except Exception:
        continue

# 1. 自動配置 PDO
master.config_map()

# 依照 0x1600 / 0x1A00 結構 (<Hiib3x)
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

# 2. 推進至 OP 狀態
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
print("[*] 正在讀取 ESC 暫存器 (1ms PDO 持續發送中, CW=0x0006):")
print("=" * 70)

# FPRD 讀取封裝
def read_reg_raw(addr, length):
    if soem_lib:
        buf = (ctypes.c_uint8 * length)()
        # ecx_FPRD(ecx_contextt *context, uint16 Slave, uint16 ADP, uint16 length, void *p, int timeout)
        # 兼容簡化型 ec_FPRD(uint16 Slave, uint16 ADP, uint16 length, void *p, int timeout)
        try:
            ret = soem_lib.ec_FPRD(cfg_adr, addr, length, ctypes.byref(buf), 2000)
            if ret > 0:
                return bytes(buf)
        except Exception:
            pass
    return None

def read_u16(addr):
    b = read_reg_raw(addr, 2)
    return struct.unpack("<H", b)[0] if b else None

def read_u8(addr):
    b = read_reg_raw(addr, 1)
    return struct.unpack("<B", b)[0] if b else None

al_status      = read_u16(0x0130)
al_status_code = read_u16(0x0134)
al_event_mask  = read_u16(0x0204)
al_event_req   = read_u16(0x0220)

if al_status is not None:
    print(f"1. AL 狀態暫存器:")
    print(f"   0x0130 AL Status        : 0x{al_status:04X}")
    print(f"   0x0134 AL Status Code   : 0x{al_status_code:04X}")
    print(f"\n2. PDI 中斷事件暫存器:")
    print(f"   0x0204 AL Event Mask    : 0x{al_event_mask:04X}")
    print(f"   0x0220 AL Event Request : 0x{al_event_req:04X}")
    sm2_in_req  = bool(al_event_req & 0x0100)
    sm2_in_mask = bool(al_event_mask & 0x0100)
    print(f"   >> SM2 Event (Bit 8) in AL Event Request : {sm2_in_req}")
    print(f"   >> SM2 Event (Bit 8) in AL Event Mask    : {sm2_in_mask}")
    
    print(f"\n3. SM2 物理暫存器 (0x0810 ~ 0x0817):")
    print(f"   0x0810 Start Addr : 0x{read_u16(0x0810):04X}")
    print(f"   0x0812 Length     : {read_u16(0x0812)} Bytes")
    print(f"   0x0814 Control    : 0x{read_u8(0x0814):02X}")
    print(f"   0x0815 Status     : 0x{read_u8(0x0815):02X}")
    print(f"   0x0816 Activate   : 0x{read_u8(0x0816):02X}")
    print(f"   0x0817 PDI Control: 0x{read_u8(0x0817):02X}")
else:
    print("[-] Python ctypes 無法直接呼叫系統 libsoem.so，改以原生 C 診斷。")

print(f"\n當前即時通訊 WKC = {last_wkc}")

running = False
th.join()
slave.output = bytes(len(slave.output))
master.send_processdata()
master.close()
