import pysoem
import struct
import time

master = pysoem.Master()
master.open("eth0")

if master.config_init() <= 0:
    print("[-] 找不到從站！")
    exit()

slave = master.slaves[0]
print(f"[+] 找到從站: {slave.name}")

# 1. 推進至 PREOP 確保 Mailbox (CoE) 就緒
slave.state = pysoem.PREOP_STATE
master.write_state()
master.state_check(pysoem.PREOP_STATE, 50000)
time.sleep(0.3)
master.read_state()
print(f"[+] 當前狀態: 0x{slave.state:02X} (0x02 代表 PREOP，Mailbox 已開啟)")

targets = [
    (0x31D3, 0, "adcK0"),
    (0x31D4, 0, "adcK"),
    (0x31D5, 0, "adcSTD"),
    (0x31E4, 0, "angREL"),
    (0x31E5, 0, "angPOLE"),
    (0x31E7, 0, "angINV"),
    (0x33FE, 0, "romVERS"),
    (0x33FF, 0, "romDATE"),
]

print("\n" + "=" * 70)
print("1. PREOP 狀態下精確讀取 0x31xx / 0x33xx 參數")
print("=" * 70)

found_any = False
for idx, sub, desc in targets:
    try:
        raw = slave.sdo_read(idx, sub)
        found_any = True
        val = int.from_bytes(raw, byteorder='little')
        print(f"  0x{idx:04X}:{sub:02d} | 讀取成功: {val} (0x{val:X}) | {desc}")
    except Exception as e:
        print(f"  0x{idx:04X}:{sub:02d} | 讀取失敗: {e} | {desc}")

# 2. 如果 0x31xx 完全不存在，掃描 0x2000~0x5FFF 區域看是否有其他私有物件
if not found_any:
    print("\n" + "=" * 70)
    print("2. 0x31xx 不存在，快速探測私有物件區塊 (0x2000 ~ 0x5FFF)...")
    print("=" * 70)
    found_objs = []
    # 抽樣掃描常見 base 地址
    probe_list = [0x2000, 0x2100, 0x2800, 0x3000, 0x3001, 0x4000, 0x5000]
    for idx in probe_list:
        try:
            raw = slave.sdo_read(idx, 0)
            val = int.from_bytes(raw, byteorder='little')
            found_objs.append((idx, val))
            print(f"  [發現物件] 0x{idx:04X}:00 = 0x{val:X}")
        except Exception:
            pass

    if not found_objs:
        print("  私有區塊探測未發現物件，該韌體可能未啟用私有物件字典。")

master.close()
