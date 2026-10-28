from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QLabel, QPushButton, QLineEdit, QTextEdit, QGridLayout
)
from PyQt5.QtGui import QTextCursor

from core.cia402_def import parse_cia402_state


class MainWindow(QMainWindow):
    def __init__(self, master_thread, config: dict):
        super().__init__()
        self.cfg = config
        self.ecat_thread = master_thread
        self.setWindowTitle("AX58200 CiA 402 關節模組控制上位機")
        self.resize(780, 820)

        self.heartbeat = False
        self.actual_position = 0
        self.mode_display = 0
        self.jog_step = int(config.get("joint", {}).get("jog_step", 1000))
        self.init_ui()

        self.ecat_thread.log_message.connect(self.append_log)
        self.ecat_thread.telemetry.connect(self.on_telemetry)
        if not self.ecat_thread.isRunning():
            self.ecat_thread.start()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        diag_group = QGroupBox("EtherCAT 通訊即時傳輸診斷 (Bus Traffic & Performance)")
        diag_layout = QGridLayout(diag_group)

        self.lbl_txrx_led = QLabel("● 等待連線")
        self.lbl_txrx_led.setStyleSheet("color: gray; font-weight: bold; font-size: 14px;")
        self.lbl_cycle_time = QLabel("實際週期: -- ms")
        self.lbl_rtt = QLabel("傳輸耗時 (RTT): -- ms")
        self.lbl_frames = QLabel("累計封包: 0")
        self.lbl_lost = QLabel("遺失封包: 0")
        self.lbl_lost.setStyleSheet("color: #27ae60;")

        diag_layout.addWidget(self.lbl_txrx_led, 0, 0)
        diag_layout.addWidget(self.lbl_cycle_time, 0, 1)
        diag_layout.addWidget(self.lbl_rtt, 0, 2)
        diag_layout.addWidget(self.lbl_frames, 1, 0)
        diag_layout.addWidget(self.lbl_lost, 1, 1)
        main_layout.addWidget(diag_group)

        status_group = QGroupBox("從站即時狀態 (EtherCAT / CiA 402)")
        status_layout = QGridLayout(status_group)

        self.lbl_state = QLabel("狀態: 未連線")
        self.lbl_state.setStyleSheet("font-size: 18px; font-weight: bold; color: gray;")
        self.lbl_flow = QLabel("流程: 等待主站")
        self.lbl_sw = QLabel("Statusword: 0x0000")
        self.lbl_pos = QLabel("實際位置 (0x6064): 0")
        self.lbl_vel = QLabel("實際速度 (0x606C): 0")
        self.lbl_mode = QLabel("運行模式 (0x6061): 0")
        self.lbl_wkc = QLabel("WKC: --")
        self.lbl_err = QLabel("錯誤碼 (0x603F): 無")

        status_layout.addWidget(self.lbl_state, 0, 0, 1, 2)
        status_layout.addWidget(self.lbl_flow, 1, 0, 1, 2)
        status_layout.addWidget(self.lbl_sw, 2, 0)
        status_layout.addWidget(self.lbl_pos, 2, 1)
        status_layout.addWidget(self.lbl_vel, 3, 0)
        status_layout.addWidget(self.lbl_mode, 3, 1)
        status_layout.addWidget(self.lbl_wkc, 4, 0)
        status_layout.addWidget(self.lbl_err, 4, 1)
        main_layout.addWidget(status_group)

        btn_group = QGroupBox("狀態機使能與運轉控制 (Operation Flow)")
        btn_layout = QHBoxLayout(btn_group)

        self.btn_reset = QPushButton("故障清除\n(Fault Reset)")
        self.btn_reset.setStyleSheet("background-color: #f39c12; color: white; font-weight: bold; padding: 10px;")
        self.btn_reset.clicked.connect(self.request_fault_reset)

        self.btn_shutdown = QPushButton("退出激磁\n(Shutdown)")
        self.btn_shutdown.setStyleSheet("background-color: #3498db; color: white; font-weight: bold; padding: 10px;")
        self.btn_shutdown.clicked.connect(self.request_shutdown)

        self.btn_enable = QPushButton("1. 激磁使能\n(Servo ON)")
        self.btn_enable.setStyleSheet("background-color: #2ecc71; color: white; font-weight: bold; padding: 10px;")
        self.btn_enable.clicked.connect(self.request_enable)

        self.btn_homing = QPushButton("2. 向量尋零\n(Homing)")
        self.btn_homing.setStyleSheet("background-color: #9b59b6; color: white; font-weight: bold; padding: 10px;")
        self.btn_homing.clicked.connect(self.request_homing)

        self.btn_csp = QPushButton("3. 啟動位置\n(CSP ON)")
        self.btn_csp.setStyleSheet("background-color: #16a085; color: white; font-weight: bold; padding: 10px;")
        self.btn_csp.clicked.connect(self.switch_to_csp)

        self.btn_quickstop = QPushButton("急停\n(Quick Stop)")
        self.btn_quickstop.setStyleSheet("background-color: #e74c3c; color: white; font-weight: bold; padding: 10px;")
        self.btn_quickstop.clicked.connect(self.request_quick_stop)

        for button in (
            self.btn_reset, self.btn_shutdown, self.btn_enable,
            self.btn_homing, self.btn_csp, self.btn_quickstop,
        ):
            btn_layout.addWidget(button)
        main_layout.addWidget(btn_group)

        jog_group = QGroupBox("位置模式 (CSP) 點動設定 — 目標會平滑插補，不會一個週期跳完")
        jog_layout = QHBoxLayout(jog_group)
        jog_layout.addWidget(QLabel("目標位置 (0x607A):"))
        self.edit_target_pos = QLineEdit("0")
        jog_layout.addWidget(self.edit_target_pos)

        self.btn_send_pos = QPushButton("發送目標位置")
        self.btn_send_pos.clicked.connect(self.send_custom_pos)
        jog_layout.addWidget(self.btn_send_pos)

        self.btn_jog_p = QPushButton(f"JOG +{self.jog_step}")
        self.btn_jog_p.clicked.connect(lambda: self.jog_step_cmd(self.jog_step))
        jog_layout.addWidget(self.btn_jog_p)

        self.btn_jog_n = QPushButton(f"JOG -{self.jog_step}")
        self.btn_jog_n.clicked.connect(lambda: self.jog_step_cmd(-self.jog_step))
        jog_layout.addWidget(self.btn_jog_n)
        main_layout.addWidget(jog_group)

        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        main_layout.addWidget(QLabel("系統日誌:"))
        main_layout.addWidget(self.txt_log)

    def append_log(self, text: str):
        if not text:
            return
        for line in text.split("\n"):
            self.txt_log.append(line)
        # 通訊恢復時若舊日誌留著，視窗會越積越慢
        if self.txt_log.document().blockCount() > 500:
            self.txt_log.clear()
            self.txt_log.append("[*] 日誌超過 500 行，已截斷")
        self.txt_log.moveCursor(QTextCursor.End)

    def on_telemetry(self, data: dict):
        self.heartbeat = not self.heartbeat
        dot = "●" if self.heartbeat else "○"
        link_ok = bool(data.get("link_ok"))
        if link_ok:
            self.lbl_txrx_led.setText(f"{dot} 資料傳輸中")
            self.lbl_txrx_led.setStyleSheet("color: #27ae60; font-weight: bold; font-size: 14px;")
        else:
            self.lbl_txrx_led.setText(f"{dot} 通訊中斷")
            self.lbl_txrx_led.setStyleSheet("color: red; font-weight: bold; font-size: 14px;")

        self.lbl_cycle_time.setText(f"實際週期: {data.get('cycle_ms', 0.0):.2f} ms")
        self.lbl_rtt.setText(f"傳輸耗時 (RTT): {data.get('rtt_ms', 0.0):.2f} ms")
        self.lbl_frames.setText(f"累計封包: {data.get('frame_count', 0)}")
        lost = data.get("lost_count", 0)
        self.lbl_lost.setText(f"遺失封包: {lost}")
        self.lbl_lost.setStyleSheet("color: red; font-weight: bold;" if lost else "color: #27ae60;")

        self.actual_position = int(data.get("actual_position", 0))
        self.mode_display = int(data.get("op_mode_display", 0))
        sw = int(data.get("statusword", 0))
        wkc = data.get("wkc", 0)
        expected = data.get("expected_wkc", 0)
        self.lbl_flow.setText(f"流程: {data.get('flow', '閒置')}")
        self.lbl_sw.setText(f"Statusword: 0x{sw:04X}")
        self.lbl_pos.setText(f"實際位置 (0x6064): {self.actual_position}")
        self.lbl_vel.setText(f"實際速度 (0x606C): {data.get('actual_velocity', 0)}")
        self.lbl_mode.setText(f"運行模式 (0x6061): {self.mode_display}")
        if link_ok:
            self.lbl_wkc.setText(f"WKC: {wkc} / {expected}")
        else:
            self.lbl_wkc.setText(f"WKC: {wkc} / {expected} (異常)")

        if link_ok:
            state_str, color = parse_cia402_state(sw)
            self.lbl_state.setText(f"狀態: {state_str}")
            self.lbl_state.setStyleSheet(f"font-size: 18px; font-weight: bold; color: {color};")
        else:
            self.lbl_state.setText("狀態: 通訊中斷")
            self.lbl_state.setStyleSheet("font-size: 18px; font-weight: bold; color: red;")

        err_code = int(data.get("err_code", 0))
        if err_code:
            self.lbl_err.setText(f"錯誤碼 (0x603F): 0x{err_code:04X}")
            self.lbl_err.setStyleSheet("color: red; font-weight: bold;")
        else:
            self.lbl_err.setText("錯誤碼 (0x603F): 無")
            self.lbl_err.setStyleSheet("color: green;")

    def request_fault_reset(self):
        self.append_log("[Cmd] 故障復位：先送 0x0000，再送 0x0080")
        self.ecat_thread.request_fault_reset()

    def request_shutdown(self):
        self.append_log("[Cmd] 退出激磁 (Shutdown 0x0006)")
        self.ecat_thread.request_shutdown()

    def request_enable(self):
        self.append_log("[Cmd] 步驟一：激磁。模式維持 0，直到 Operation Enabled")
        self.ecat_thread.request_servo_on()

    def request_homing(self):
        self.append_log("[Cmd] 步驟二：先退出激磁，寫入 Homing 模式後才重新使能並啟動尋零")
        self.ecat_thread.request_homing()

    def switch_to_csp(self):
        self.edit_target_pos.setText(str(self.actual_position))
        self.append_log(f"[Cmd] 步驟三：退出激磁後切換 CSP，目標鎖在 {self.actual_position}")
        self.ecat_thread.request_csp()

    def request_quick_stop(self):
        self.append_log("[Cmd] 急停：0x0002 後以 0x0000 關閉驅動")
        self.ecat_thread.request_quick_stop()

    def send_custom_pos(self):
        try:
            val = int(self.edit_target_pos.text())
        except ValueError:
            self.append_log("[-] 輸入的目標位置格式錯誤")
            return
        self.ecat_thread.request_move_to(val)

    def jog_step_cmd(self, step: int):
        self.ecat_thread.request_jog(step)

    def closeEvent(self, event):
        self.ecat_thread.stop()
        event.accept()
