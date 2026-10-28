import os
import sys
import pysoem

IFNAME = "eth0"
# Label nama berkas FoE harus sama persis dengan aturan manual AX58200 (tanpa ekstensi .efw)
FOE_FILENAME = "AX58200_BL60M24D8E00430080_MTMBL60"
FILE_PATH = "/home/nvidia/Desktop/ax58200_joint_controller/AX58200_BL60M24D8E00430080_MTMBL60.efw"

if not os.path.exists(FILE_PATH):
    print(f"[-] Berkas tidak ditemukan: {FILE_PATH}")
    sys.exit(1)

with open(FILE_PATH, "rb") as f:
    file_bytes = f.read()

print(f"[*] Berhasil membaca berkas parameter, ukuran: {len(file_bytes)} bytes")

master = pysoem.Master()
try:
    master.open(IFNAME)
except Exception as e:
    print(f"[-] Gagal membuka antarmuka jaringan {IFNAME}: {e}")
    sys.exit(1)

if master.config_init() <= 0:
    print("[-] Tidak ada slave yang terdeteksi!")
    master.close()
    sys.exit(1)

slave = master.slaves[0]
print(f"[+] Ditemukan slave: {slave.name}")

# Pindahkan status slave ke PRE-OP untuk memastikan Mailbox aktif
slave.state = pysoem.PREOP_STATE
master.write_state()
master.state_check(pysoem.PREOP_STATE, 50000)

print(f"[*] Mengirim berkas parameter via FoE (Target: {FOE_FILENAME})...")
try:
    slave.foe_write(FOE_FILENAME, 0, file_bytes)
    print("[+] Berhasil! Berkas parameter sukses ditulis melalui FoE!")
except Exception as e:
    print(f"[!] Penulisan pada status PRE-OP gagal: {e}. Mencoba pada status BOOT...")
    try:
        slave.state = pysoem.BOOT_STATE
        master.write_state()
        master.state_check(pysoem.BOOT_STATE, 50000)
        slave.foe_write(FOE_FILENAME, 0, file_bytes)
        print("[+] Penulisan FoE berhasil pada status BOOT!")
    except Exception as e_boot:
        print(f"[-] Penulisan pada status BOOT juga gagal: {e_boot}")

master.close()