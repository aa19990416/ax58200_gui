import time
import struct
import pysoem
from PyQt5.QtCore import QThread, pyqtSignal
from core.pdo_struct import PdoHandler

class EtherCATMasterThread(QThread):
    # 信號定義傳送 5 個參數: (狀態字典, WKC, 錯誤碼, 日誌訊息, 統計字典)
    data_received = pyqtSignal(dict, int, int, str, dict)

    def __init__(self, config: dict):
        super().__init__()
        self.cfg = config
        self.running = True
        self.master = None
        self.slave = None
        
        # 狀態控制標誌
        self.auto_enable = False
        self.auto_homing = False

        # 開機初始設定：進入 OP 後預設送 Shutdown (0x0006) 與 No Mode (0)，避免鎖死在 0x1200
        self.controlword = 0x0006
        self.target_position = 0
        self.target_velocity = 0
        self.op_mode = 0

    def run(self):
        ifname = self.cfg['ethercat']['ifname']
        self.master = pysoem.Master()

        try:
            self.master.open(ifname)
        except Exception as e:
            self.data_received.emit({}, 0, 0, f"[-] 無法開啟網卡 {ifname}: {e}", {})
            return

        if self.master.config_init() <= 0:
            self.data_received.emit({}, 0, 0, f"[-] 在 {ifname} 上未發現從站！", {})
            self.master.close()
            return

        self.slave = self.master.slaves[0]
        self.master.config_map()

        # 配置 1ms (1,000,000 ns) 分散式時鐘 (DC)
        cycle_ns = 1_000_000
        try:
            self.slave.dc_sync(True, cycle_ns)
            self.master.config_dc()
        except Exception as e:
            print(f"[!] DC 時鐘配置提示: {e}")

        safeop_to = self.cfg['ethercat'].get('safeop_timeout_us', 50000)
        if self.master.state_check(pysoem.SAFEOP_STATE, safeop_to) != pysoem.SAFEOP_STATE:
            self.data_received.emit({}, 0, 0, "[-] 無法進入 SAFE_OP 狀態", {})
            self.master.close()
            return

        # 寫入同步週期 (1ms) 至 0x1C32:02 與 0x1C33:02
        try:
            self.slave.sdo_write(0x1C32, 2, struct.pack("<I", cycle_ns))
            self.slave.sdo_write(0x1C33, 2, struct.pack("<I", cycle_ns))
        except Exception:
            pass

        # 推進到 OPERATIONAL (OP) 狀態
        self.slave.state = pysoem.OP_STATE
        self.master.write_state()
        op_to = self.cfg['ethercat'].get('op_timeout_us', 50000)
        self.master.state_check(pysoem.OP_STATE, op_to)

        if self.slave.state != pysoem.OP_STATE:
            self.data_received.emit({}, 0, 0, f"[-] 無法進入 OP 狀態，目前狀態: {hex(self.slave.state)}", {})
            self.master.close()
            return

        self.data_received.emit({}, 0, 0, f"[+] 成功連接從站: {self.slave.name} 並進入 OP 狀態！", {})

        recv_to = self.cfg['ethercat'].get('recv_timeout_us', 2000)
        target_cycle_sec = 0.001  # 1ms
        
        frame_count = 0
        lost_count = 0
        last_time = time.perf_counter()

        while self.running:
            t0 = time.perf_counter()

            # 1. 發送 RxPDO
            self.slave.output = PdoHandler.pack_rx(
                self.controlword, self.target_position, self.target_velocity, self.op_mode
            )
            self.master.send_processdata()
            frame_count += 1

            # 2. 接收 TxPDO
            wkc = self.master.receive_processdata(recv_to)
            t_recv = time.perf_counter()

            rtt_ms = (t_recv - t0) * 1000.0
            actual_cycle_ms = (t0 - last_time) * 1000.0
            last_time = t0

            stats = {
                "frame_count": frame_count,
                "lost_count": lost_count,
                "rtt_ms": rtt_ms,
                "cycle_ms": actual_cycle_ms
            }

            if wkc > 0:
                tx_dict = PdoHandler.unpack_tx(self.slave.input)
                sw = tx_dict['statusword']

                # 讀取錯誤代碼 (若有 Fault 則讀 0x603F)
                err_code = 0
                if sw & 0x0008:
                    try:
                        err_bytes = self.slave.sdo_read(0x603F, 0)
                        err_code = struct.unpack("<H", err_bytes)[0]
                    except Exception:
                        err_code = 0xFFFF

                log_msg = ""

                # --- 官方標準狀態機流程：Servo ON -> Homing -> CSP ---
                if self.auto_enable:
                    # 故障復位 (Bit 3 為 1)
                    if sw & 0x0008:
                        self.controlword = 0x0080
                    # 若卡在 0x1200 (Not Ready to Switch On) 或待命 (0x1240) -> 下發 Shutdown (0x0006)
                    elif (sw & 0x004F) == 0x0000 or (sw & 0x004F) == 0x0040:
                        self.op_mode = 0
                        self.controlword = 0x0006
                    # 準備就緒 (0x1221 / Ready to Switch On) -> 下發 Switch On (0x0007)
                    elif (sw & 0x006F) == 0x0021:
                        self.controlword = 0x0007
                    # 已上電就緒 (0x1223 / Switched On) -> 下發 Enable Operation (0x000F)
                    elif (sw & 0x006F) == 0x0023:
                        self.target_position = tx_dict['actual_position']
                        self.controlword = 0x000F
                    # 激磁成功 (0x1227 / Operation Enabled)
                    elif (sw & 0x006F) == 0x0027:
                        self.auto_enable = False
                        log_msg = "[+] 伺服使能成功 (Servo ON 達成，馬達軸已鎖定)！"

                # 處理 Homing 尋零過程
                if self.auto_homing:
                    # Bit 12: Homing attained (原點達成), Bit 10: Target reached
                    if (sw & 0x1400) == 0x1400 or (sw & 0x1000):
                        self.auto_homing = False
                        # Homing 完成後無縫就緒 CSP 模式 (模式 8)
                        self.op_mode = 8
                        self.target_position = tx_dict['actual_position']
                        self.controlword = 0x000F
                        log_msg = "[+] Homing 向量尋零完成！已自動就緒 CSP 位置模式 (0x6060=8)。"

                self.data_received.emit(tx_dict, wkc, err_code, log_msg, stats)
            else:
                lost_count += 1
                stats["lost_count"] = lost_count
                self.data_received.emit({}, 0, 0, "[!] 警告: Working Counter 遺失 (通訊超時)", stats)

            elapsed = time.perf_counter() - t0
            sleep_time = target_cycle_sec - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

        # 安全關閉
        self.slave.output = PdoHandler.pack_rx(0x0000, 0, 0, 0)
        self.master.send_processdata()
        time.sleep(0.01)
        self.master.close()

    def request_auto_enable(self):
        """步驟一：Servo ON (在模式 0 下激磁)"""
        self.op_mode = 0
        self.auto_enable = True

    def trigger_homing(self):
        """步驟二：Homing (切換模式 6 並啟動尋零)"""
        self.op_mode = 6
        self.controlword = 0x001F
        self.auto_homing = True

    def set_op_mode(self, mode: int):
        self.op_mode = mode

    def set_controlword(self, cw: int):
        self.controlword = cw

    def set_target_position(self, pos: int):
        self.target_position = int(pos)

    def stop(self):
        self.running = False
        self.wait()