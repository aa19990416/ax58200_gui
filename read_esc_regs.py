import pysoem
import struct

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到從站！")
    exit()

slave = master.slaves[0]

def read_esc_word(offset):
    # 使用 APRD/FPRD 讀取 ESC 實體暫存器
    try:
        data = slave.reg_read(offset, 2)
        return struct.unpack("<H", data)[0]
    except Exception as e:
        return None

print(f"[+] 連線從站: {slave.name}")
al_stat = read_esc_word(0x0130)
al_code = read_esc_word(0x0134)
print(f"[*] 開機狀態 -> AL Status (0x130): {hex(al_stat) if al_stat is not None else 'N/A'}, AL Status Code (0x134): {hex(al_code) if al_code is not None else 'N/A'}")

# 嘗試映射並推進至 OP
master.config_map()
slave.state = pysoem.OP_STATE
master.write_state()
master.state_check(pysoem.OP_STATE, 50000)

al_stat_op = read_esc_word(0x0130)
al_code_op = read_esc_word(0x0134)
print(f"[*] 推進後狀態 -> AL Status (0x130): {hex(al_stat_op) if al_stat_op is not None else 'N/A'}, AL Status Code (0x134): {hex(al_code_op) if al_code_op is not None else 'N/A'}")

master.close()
