import pysoem
import time
import struct

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到從站！")
    exit()

slave = master.slaves[0]

# 1. 讀取 SDO 錯誤碼
def check_error():
    try:
        err = struct.unpack("<H", slave.sdo_read(0x603F, 0))[0]
        print(f"[*] 0x603F (Error Code): {hex(err)}")
        return err
    except Exception as e:
        print(f"[-] 讀取 0x603F 失敗: {e}")
        return 0

check_error()

# 2. 配置 DC 與映射
cycle_ns = 1_000_000
try:
    slave.dc_sync(True, cycle_ns)
    master.config_dc()
except Exception:
    pass

master.config_map()
master.state_check(pysoem.SAFEOP_STATE, 50000)

slave.state = pysoem.OP_STATE
master.write_state()
master.state_check(pysoem.OP_STATE, 50000)

print(f"[+] 進入 OP 狀態")

def pack_rx(cw, pos, vel, mode):
    return struct.pack("<HiiBxxx", cw, pos, vel, mode)

def unpack_tx(data):
    if len(data) >= 11:
        return struct.unpack("<HiiB", data[:11])
    return 0, 0, 0, 0

# 建立具備嚴格邊緣觸發的控制序列
# 0: CW=0x0000 (確保 PreControlword=0)
# 1: CW=0x0080 (製造 0 -> 1 上升沿清除故障)
# 2: CW=0x0000 (清除復位旗標)
# 3: CW=0x0006 (Shutdown -> 期待進入 0x1221 / 0x1240)
# 4: CW=0x0007 (Switch On -> 期待進入 0x1223)
# 5: CW=0x000F (Enable Operation -> 期待進入 0x1227)
sequence = [
    (0x0000, 200, "歸零 CW (製造下降沿)"),
    (0x0080, 300, "下發 Fault Reset (上升沿)"),
    (0x0000, 200, "復位完成釋放"),
    (0x0006, 500, "Shutdown (切換就緒)"),
    (0x0007, 500, "Switch On (上電就緒)"),
    (0x000F, 1000, "Enable Operation (激磁使能)"),
]

target_cycle_sec = 0.001
last_sw = None

for cw, duration_ms, desc in sequence:
    print(f"\n[*] 階段: {desc} (CW=0x{cw:04X})")
    for i in range(duration_ms):
        t0 = time.perf_counter()

        wkc = master.receive_processdata(2000)
        sw, cur_pos, cur_vel, disp_mode = unpack_tx(slave.input) if (wkc > 0 and slave.input) else (0, 0, 0, 0)

        if sw != last_sw or (i % 200 == 0):
            print(f"    [{i:4d}ms] SW: 0x{sw:04X} | Pos: {cur_pos:6d} | Mode: {disp_mode}")
            last_sw = sw

        slave.output = pack_rx(cw, cur_pos, 0, 8)
        master.send_processdata()

        elapsed = time.perf_counter() - t0
        sleep_t = target_cycle_sec - elapsed
        if sleep_t > 0:
            time.sleep(sleep_t)

slave.output = bytes(14)
master.send_processdata()
time.sleep(0.01)

# 結束後再次檢查錯誤碼
check_error()

master.close()
