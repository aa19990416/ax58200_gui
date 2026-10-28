import pysoem
import time
import struct

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到從站！")
    exit()

slave = master.slaves[0]
print(f"[+] 連接從站: {slave.name}")

# 1. 自動映射
master.config_map()

# 2. 推進至 PREOP
slave.state = pysoem.PREOP_STATE
master.write_state()
master.state_check(pysoem.PREOP_STATE, 50000)

# 3. 推進至 SAFEOP
slave.state = pysoem.SAFEOP_STATE
master.write_state()
master.state_check(pysoem.SAFEOP_STATE, 50000)

# 4. 【關鍵】發送第一筆有效 Process Data 激活 SM2
def pack_rx(cw, pos, vel, mode):
    return struct.pack("<HiiBxxx", cw, pos, vel, mode)

def unpack_tx(data):
    if len(data) >= 11:
        return struct.unpack("<HiiB", data[:11])
    return 0, 0, 0, 0

# 預先塞入乾淨的輸出封包
slave.output = pack_rx(0x0000, 0, 0, 8)
master.send_processdata()
master.receive_processdata(2000)

# 5. 推進至 OP 狀態
slave.state = pysoem.OP_STATE
master.write_state()

# 在等待進入 OP 的同時持續打 Process Data 避免被踢出
for _ in range(50):
    master.send_processdata()
    master.receive_processdata(2000)
    time.sleep(0.002)

master.state_check(pysoem.OP_STATE, 50000)
print(f"[+] 進入 OP 狀態: {hex(slave.state)}")

# 6. 連續發送標準 CiA 402 解鎖脈衝 (Fault Reset 0x80 -> Shutdown 0x06 -> Switch On 0x07 -> Enable 0x0F)
sequence = [
    (0x0080, 200, "1. 下發 Fault Reset (0x0080)"),
    (0x0000, 100, "2. 復位清零 (0x0000)"),
    (0x0006, 300, "3. 下發 Shutdown (0x0006 -> 預期 0x1221/0x1240)"),
    (0x0007, 300, "4. 下發 Switch On (0x0007 -> 預期 0x1223)"),
    (0x000F, 500, "5. 下發 Enable Operation (0x000F -> 預期 0x1227)"),
]

for cw, duration_ms, desc in sequence:
    print(f"\n[*] 執行階段: {desc}")
    for i in range(duration_ms):
        t0 = time.perf_counter()

        # 先送出 outputs 觸發從站 PDI_Isr
        slave.output = pack_rx(cw, 0, 0, 8)
        master.send_processdata()

        wkc = master.receive_processdata(2000)
        if wkc > 0 and slave.input:
            sw, cur_pos, cur_vel, disp_mode = unpack_tx(slave.input)
            if i % 100 == 0:
                print(f"    [{i:3d}ms] SW: 0x{sw:04X} | Pos: {cur_pos:6d} | Mode: {disp_mode}")

        sleep_t = 0.001 - (time.perf_counter() - t0)
        if sleep_t > 0:
            time.sleep(sleep_t)

master.close()
print("\n[*] 測試完成。")
