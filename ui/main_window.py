import sys
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QLabel, QPushButton, QLineEdit, QTextEdit, QGridLayout
)
from PyQt5.QtCore import Qt
from core.cia402_def import (
    parse_cia402_state, CMD_FAULT_RESET, CMD_SHUTDOWN, 
    CMD_SWITCH_ON, CMD_ENABLE_OPERATION, CMD_QUICK_STOP
)

class MainWindow(QMainWindow):
    def __init__(self, master_thread, config: dict):
        super().__init__()
        self.cfg = config
        self.ecat_thread = master_thread  # 接收 main.py 實例化之執行緒
        self.setWindowTitle("AX58200 CiA 402 關節模組控制上位機")
        self.resize(780, 800)

        self.heartbeat = False
        self.init_ui()

        # 連接通訊信號並啟動執行緒
        self.ecat_thread.data_received.connect(self.on_data_received)
        if not self.ecat_thread.isRunning():
            self.ecat_thread.start()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # ----------------------------------------------------
        # 1. 通訊鏈路與傳輸診斷 GroupBox
        # ----------------------------------------------------
        diag_group = QGroupBox("EtherCAT 通訊即時傳輸診斷 (Bus Traffic & Performance)")
        diag_layout = QGridLayout(diag_group)

        self.lbl_txrx_led = QLabel("● 資料傳輸中")
        self.lbl_txrx_led.setStyleSheet("color: #27ae60; font-weight: bold; font-size: 14px;")

        self.lbl_cycle_time = QLabel("實際週期: 0.00 ms")
        self.lbl_rtt = QLabel("傳輸耗時 (RTT): 0.00 ms")
        self.lbl_frames = QLabel("累計封包: 0")
        self.lbl_lost = QLabel("遺失封包: 0")
        self.lbl_lost.setStyleSheet("color: #27ae60;")

        diag_layout.addWidget(self.lbl_txrx_led, 0, 0)
        diag_layout.addWidget(self.lbl_cycle_time, 0, 1)
        diag_layout.addWidget(self.lbl_rtt, 0, 2)
        diag_layout.addWidget(self.lbl_frames, 1, 0)
        diag_layout.addWidget(self.lbl_lost, 1, 1)

        main_layout.addWidget(diag_group)

        # ----------------------------------------------------
        # 2. 從站即時狀態 GroupBox
        # ----------------------------------------------------
        status_group = QGroupBox("從站即時狀態 (EtherCAT / CiA 402)")
        status_layout = QGridLayout(status_group)

        self.lbl_state = QLabel("狀態: 未連線")
        self.lbl_state.setStyleSheet("font-size: 18px; font-weight: bold; color: gray;")
        
        self.lbl_sw = QLabel("Statusword: 0x0000")
        self.lbl_pos = QLabel("實際位置 (0x6064): 0")
        self.lbl_vel = QLabel("實際速度 (0x606C): 0")
        self.lbl_mode = QLabel("運行模式 (0x6061): 0")
        self.lbl_wkc = QLabel("WKC: 0")
        self.lbl_err = QLabel("錯誤碼 (0x603F): 無")

        status_layout.addWidget(self.lbl_state, 0, 0, 1, 2)
        status_layout.addWidget(self.lbl_sw, 1, 0)
        status_layout.addWidget(self.lbl_pos, 1, 1)
        status_layout.addWidget(self.lbl_vel, 2, 0)
        status_layout.addWidget(self.lbl_mode, 2, 1)
        status_layout.addWidget(self.lbl_wkc, 3, 0)
        status_layout.addWidget(self.lbl_err, 3, 1)

        main_layout.addWidget(status_group)

        # ----------------------------------------------------
        # 3. 官方標準流程控制按鈕組 (Servo ON -> Homing -> CSP)
        # ----------------------------------------------------
        btn_group = QGroupBox("狀態機使能與運轉控制 (Operation Flow)")
        btn_layout = QHBoxLayout(btn_group)

        self.btn_reset = QPushButton("故障清除\n(Fault Reset)")
        self.btn_reset.setStyleSheet("background-color: #f39c12; color: white; font-weight: bold; padding: 10px;")
        self.btn_reset.clicked.connect(lambda: self.send_cmd(CMD_FAULT_RESET))

        self.btn_shutdown = QPushButton("進入準備\n(Shutdown 0x06)")
        self.btn_shutdown.setStyleSheet("background-color: #3498db; color: white; font-weight: bold; padding: 10px;")
        self.btn_shutdown.clicked.connect(lambda: self.send_cmd(CMD_SHUTDOWN))

        self.btn_enable = QPushButton("1. 激磁使能\n(Servo ON)", self)
        self.btn_enable.setStyleSheet("background-color: #2ecc71; color: white; font-weight: bold; padding: 10px;")
        self.btn_enable.clicked.connect(self.request_enable)

        self.btn_homing = QPushButton("2. 向量尋零\n(Homing)", self)
        self.btn_homing.setStyleSheet("background-color: #9b59b6; color: white; font-weight: bold; padding: 10px;")
        self.btn_homing.clicked.connect(self.request_homing)

        self.btn_csp = QPushButton("3. 啟動位置\n(CSP ON)", self)
        self.btn_csp.setStyleSheet("background-color: #16a085; color: white; font-weight: bold; padding: 10px;")
        self.btn_csp.clicked.connect(self.switch_to_csp)

        self.btn_quickstop = QPushButton("急停\n(Quick Stop)")
        self.btn_quickstop.setStyleSheet("background-color: #e74c3c; color: white; font-weight: bold; padding: 10px;")
        self.btn_quickstop.clicked.connect(lambda: self.send_cmd(CMD_QUICK_STOP))

        btn_layout.addWidget(self.btn_reset)
        btn_layout.addWidget(self.btn_shutdown)
        btn_layout.addWidget(self.btn_enable)
        btn_layout.addWidget(self.btn_homing)
        btn_layout.addWidget(self.btn_csp)
        btn_layout.addWidget(self.btn_quickstop)

        main_layout.addWidget(btn_group)

        # ----------------------------------------------------
        # 4. 運動控制面板 (CSP 點動)
        # ----------------------------------------------------
        jog_group = QGroupBox("位置模式 (CSP) 點動設定")
        jog_layout = QHBoxLayout(jog_group)

        jog_layout.addWidget(QLabel("目標位置 (0x607A):"))
        self.edit_target_pos = QLineEdit("0")
        jog_layout.addWidget(self.edit_target_pos)

        self.btn_send_pos = QPushButton("發送目標位置")
        self.btn_send_pos.clicked.connect(self.send_custom_pos)
        jog_layout.addWidget(self.btn_send_pos)

        self.btn_jog_p = QPushButton("JOG +1000")
        self.btn_jog_p.clicked.connect(lambda: self.jog_step(1000))
        jog_layout.addWidget(self.btn_jog_p)

        self.btn_jog_n = QPushButton("JOG -1000")
        self.btn_jog_n.clicked.connect(lambda: self.jog_step(-1000))
        jog_layout.addWidget(self.btn_jog_n)

        main_layout.addWidget(jog_group)

        # ----------------------------------------------------
        # 5. 系統日誌
        # ----------------------------------------------------
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        main_layout.addWidget(QLabel("系統日誌:"))
        main_layout.addWidget(self.txt_log)

    def on_data_received(self, tx_dict: dict, wkc: int, err_code: int, log_msg: str, stats: dict = None):
        if log_msg:
            self.txt_log.append(log_msg)

        # 1. 更新通訊診斷與心跳
        if stats and isinstance(stats, dict):
            self.heartbeat = not self.heartbeat
            dot = "●" if self.heartbeat else "○"
            self.lbl_txrx_led.setText(f"{dot} 資料傳輸中")
            self.lbl_txrx_led.setStyleSheet("color: #27ae60; font-weight: bold; font-size: 14px;")

            self.lbl_cycle_time.setText(f"實際週期: {stats.get('cycle_ms', 0.0):.2f} ms")
            self.lbl_rtt.setText(f"傳輸耗時 (RTT): {stats.get('rtt_ms', 0.0):.2f} ms")
            self.lbl_frames.setText(f"累計封包: {stats.get('frame_count', 0)}")
            
            lost = stats.get('lost_count', 0)
            self.lbl_lost.setText(f"遺失封包: {lost}")
            if lost > 0:
                self.lbl_lost.setStyleSheet("color: red; font-weight: bold;")
            else:
                self.lbl_lost.setStyleSheet("color: #27ae60;")

        if not tx_dict:
            self.lbl_wkc.setText(f"WKC: {wkc} (逾時)")
            return

        # 2. 更新從站狀態
        sw = tx_dict.get('statusword', 0)
        self.lbl_sw.setText(f"Statusword: 0x{sw:04X}")
        self.lbl_pos.setText(f"實際位置 (0x6064): {tx_dict.get('actual_position', 0)}")
        self.lbl_vel.setText(f"實際速度 (0x606C): {tx_dict.get('actual_velocity', 0)}")
        self.lbl_mode.setText(f"運行模式 (0x6061): {tx_dict.get('op_mode_display', 0)}")
        self.lbl_wkc.setText(f"WKC: {wkc}")

        state_str, color = parse_cia402_state(sw)
        self.lbl_state.setText(f"狀態: {state_str}")
        self.lbl_state.setStyleSheet(f"font-size: 18px; font-weight: bold; color: {color};")

        if err_code != 0:
            self.lbl_err.setText(f"錯誤碼 (0x603F): 0x{err_code:04X}")
            self.lbl_err.setStyleSheet("color: red; font-weight: bold;")
        else:
            self.lbl_err.setText("錯誤碼 (0x603F): 無")
            self.lbl_err.setStyleSheet("color: green;")

    def send_cmd(self, cw: int):
        self.ecat_thread.set_controlword(cw)
        self.txt_log.append(f"[Cmd] 手動發送 Controlword: 0x{cw:04X}")

    def request_enable(self):
        self.txt_log.append("[Cmd] 步驟一：啟動伺服激磁使能 (Servo ON)...")
        self.ecat_thread.request_auto_enable()

    def request_homing(self):
        self.txt_log.append("[Cmd] 步驟二：切換至 Homing 模式 (0x6060=6) 並啟動回原點...")
        self.ecat_thread.trigger_homing()

    def switch_to_csp(self):
        cur_pos = int(self.lbl_pos.text().split(":")[-1].strip())
        self.edit_target_pos.setText(str(cur_pos))
        self.ecat_thread.set_target_position(cur_pos)
        self.ecat_thread.set_op_mode(8)  # 切換為 CSP
        self.ecat_thread.set_controlword(0x000F)
        self.txt_log.append(f"[Cmd] 步驟三：切換至 CSP 位置模式，同步目標位置: {cur_pos}")

    def send_custom_pos(self):
        try:
            val = int(self.edit_target_pos.text())
            self.ecat_thread.set_target_position(val)
            self.txt_log.append(f"[CSP] 設定目標位置至: {val}")
        except ValueError:
            self.txt_log.append("[-] 輸入的目標位置格式錯誤！")

    def jog_step(self, step: int):
        try:
            cur = int(self.lbl_pos.text().split(":")[-1].strip())
            new_pos = cur + step
            self.edit_target_pos.setText(str(new_pos))
            self.ecat_thread.set_target_position(new_pos)
            self.txt_log.append(f"[JOG] 點動增量: {step} -> 目標位置: {new_pos}")
        except Exception as e:
            self.txt_log.append(f"[-] JOG 計算錯誤: {e}")

    def closeEvent(self, event):
        self.ecat_thread.stop()
        event.accept()