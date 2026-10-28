import pysoem
import struct
import time

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到 EtherCAT 從站！")
    exit()

slave = master.slaves[0]
print(f"[+] 找到從站: {slave.name}")

slave.state = pysoem.PREOP_STATE
master.write_state()
master.state_check(pysoem.PREOP_STATE, 50000)
time.sleep(0.1)

def sdo_read_val(idx, sub, fmt):
    try:
        data = slave.sdo_read(idx, sub)
        return struct.unpack(fmt, data)[0]
    except Exception as e:
        return f"Fail: {e}"

def reg_read_u16(addr):
    try:
        return struct.unpack("<H", slave._fprd(addr, 2))[0]
    except Exception:
        return None

al_status = reg_read_u16(0x0130)
al_code = reg_read_u16(0x0134)
err_code = sdo_read_val(0x603F, 0, "<H")
err_reg = sdo_read_val(0x1001, 0, "<B")

print("\n" + "=" * 55)
print("底層硬體與通訊故障診斷報告")
print("=" * 55)
print(f"1. AL Status       (0x0130) : 0x{al_status:04X}" if al_status else "1. AL Status: 無法讀取")
print(f"2. AL Status Code  (0x0134) : 0x{al_code:04X}" if al_code else "2. AL Status Code: 無法讀取")
print(f"3. CiA402 錯誤代碼 (0x603F) : 0x{err_code:04X}" if isinstance(err_code, int) else f"3. CiA402 錯誤代碼: {err_code}")
print(f"4. Error Register  (0x1001) : 0x{err_reg:02X}" if isinstance(err_reg, int) else f"4. Error Register: {err_reg}")

# 嘗試在 PREOP 下透過 ESC 重設從站晶片 PDI
print("\n[*] 正在測試向 0x0041 發送 PDI 硬體重設命令...")
try:
    slave._fpwr(0x0041, b'RES')
    print("[+] 重設指令已發出！")
except Exception as e:
    print(f"[-] 發送失敗: {e}")

master.close()
