import pysoem
import struct

master = pysoem.Master()
master.open("eth0")

if master.config_init() > 0:
    slave = master.slaves[0]
    print(f"[+] 成功連接: {slave.name}")

    def read_val(index, sub, name, fmt="<i"):
        try:
            raw = slave.sdo_read(index, sub)
            val = struct.unpack(fmt, raw)[0]
            print(f"  0x{index:04X}:{sub:<2} | {name:<25} = {val} ({hex(val)})")
            return val
        except Exception as e:
            print(f"  0x{index:04X}:{sub:<2} | {name:<25} = 讀取失敗 ({e})")
            return None

    print("\n--- 1. CiA 402 基礎暫存器 ---")
    read_val(0x6041, 0, "Statusword", "<H")
    read_val(0x603F, 0, "Error code", "<H")
    read_val(0x1001, 0, "Error register", "<B")

    print("\n--- 2. 參數版本確認 (手冊 Table 8-3) ---")
    read_val(0x33FE, 0, "romVERS (參數版本)")
    read_val(0x33FF, 0, "romDATE (參數日期)")

    print("\n--- 3. 電流與硬體參數 ---")
    read_val(0x31E5, 0, "angPOLE (極對數)")
    read_val(0x31E4, 0, "angREL (編碼器線數)")
    read_val(0x3157, 0, "ampLMT (電流上限)")

    master.close()
else:
    print("[-] 找不到 EtherCAT 從站！")
