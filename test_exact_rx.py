import pysoem
import time
import struct

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到從站！")
    exit()

slave = master.slaves[0]
master.config_map()

# 配置 1ms DC
cycle_ns = 1_000_000
try:
    slave.dc_sync(True, cycle_ns)
    master.config_dc()
except Exception:
    pass

master.state_check(pysoem.SAFEOP_STATE, 50000)
slave.state = pysoem.OP_STATE
master.write_state()
master.state_check(pysoem.OP_STATE, 50000)

print(f"[+] 進入 OP: {slave.name}")

# 嚴格依照 14 Bytes 打包: Controlword(2B) + TargetPos(4B) + TargetVel(4B) + OpMode(1B) + Pad(3B)
def pack_rx(cw, pos, vel, mode):
    return struct.pack("<HiiBxxx", cw, pos, vel, mode)

def unpack_tx(data):
    if len(data) >= 11:
        sw, pos, vel, mode = struct.unpack("<HiiB", data[:11])
        return sw, pos, vel, mode
    return 0, 0, 0, 0

# 測試切換序列: Fault Reset (0x80) -> Shutdown (0x06) -> Switch On (0x07)
# 同時測試模式 0 與模式 8
test_cmds = [
    (0x0080, 0, "Fault Reset (Mode 0)"),
    (0x0006, 0, "Shutdown (Mode 0)"),
    (0x0007, 0, "Switch On (Mode 0)"),
    (0x0080, 8, "Fault Reset (Mode 8)"),
    (0x0006, 8, "Shutdown (Mode 8)"),
    (0x0007, 8, "Switch On (Mode 8)"),
]

for cw, mode, desc in test_cmds:
    print(f"[*] 執行: {desc} | CW=0x{cw:04X}, Mode={mode}")
    for i in range(300):  # 每個階段持續發送 300ms
        t0 = time.perf_counter()
        slave.output = pack_rx(cw, 0, 0, mode)
        master.send_processdata()
        wkc = master.receive_processdata(2000)
        
        if wkc > 0 and i == 299:
            sw, pos, vel, disp_mode = unpack_tx(slave.input)
            raw_hex = ' '.join(f'{b:02X}' for b in slave.input)
            print(f"    -> Raw: {raw_hex} | SW: 0x{sw:04X} | ModeDisp: {disp_mode}")
            
        elapsed = time.perf_counter() - t0
        sleep_t = 0.001 - elapsed
        if sleep_t > 0:
            time.sleep(sleep_t)

slave.output = bytes(14)
master.send_processdata()
master.close()
print("[*] 測試結束")
