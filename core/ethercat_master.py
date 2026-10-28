import struct
import threading
import time

import pysoem
from PyQt5.QtCore import QThread, pyqtSignal

from core.cia402_def import (
    CMD_DISABLE_VOLTAGE,
    CMD_ENABLE_OPERATION,
    CMD_FAULT_RESET,
    CMD_QUICK_STOP,
    CMD_SHUTDOWN,
    CMD_START_HOMING,
    CMD_SWITCH_ON,
    MODE_CSP,
    MODE_HM,
    MODE_NO_MODE,
    SW_HOMING_ATTAINED,
    SW_HOMING_ERROR,
    SW_TARGET_REACHED,
    classify_cia402_state,
    mode_change_allowed,
)
from core.pdo_struct import PDO_SIZE, PdoHandler

# 常見 AL Status Code，失敗時一併印出，方便對照是看門狗還是 DC
_AL_STATUS_TEXT = {
    0x0011: "不合法的狀態切換",
    0x001A: "同步錯誤",
    0x001B: "Sync Manager 看門狗逾時",
    0x001D: "輸出設定不合法",
    0x001E: "輸入設定不合法",
    0x0025: "輸出映射不合法",
    0x0028: "不支援目前的同步模式",
    0x002C: "致命同步錯誤",
    0x002D: "沒有收到 Sync 信號",
    0x0030: "DC 同步設定不合法",
    0x0032: "DC PLL 錯誤",
    0x0035: "DC 週期不合法",
}


class EtherCATMasterThread(QThread):
    """PDO 週期執行緒。所有 SDO／PDO／控制字只在這條執行緒裡碰。"""

    log_message = pyqtSignal(str)
    telemetry = pyqtSignal(dict)

    def __init__(self, config: dict):
        super().__init__()
        self.cfg = config
        self.running = True
        self.master = None
        self.slave = None

        ec = config.get("ethercat", {})
        motion = config.get("motion", {})
        # Python 迴圈低於 1 ms 沒有意義，也避免 cycle 設成 0 時空轉
        self.cycle_s = max(0.001, float(ec.get("cycle_time_ms", 10)) / 1000.0)
        self.cycle_ns = int(round(self.cycle_s * 1_000_000_000))
        self.ifname = ec.get("ifname", "eth0")
        self.safeop_timeout_s = max(2.0, int(ec.get("safeop_timeout_us", 3_000_000)) / 1_000_000.0)
        self.op_timeout_s = max(2.0, int(ec.get("op_timeout_us", 3_000_000)) / 1_000_000.0)
        recv_us = int(ec.get("recv_timeout_us", 2000))
        half_cycle_us = int(self.cycle_s * 1_000_000 / 2)
        self.recv_timeout_us = min(recv_us, max(200, half_cycle_us))

        self.homing_speed_switch = int(motion.get("homing_speed_switch", 300))
        self.homing_speed_zero = int(motion.get("homing_speed_zero", 100))
        self.jog_speed = max(1, int(motion.get("jog_speed_cps", 2000)))
        self.jog_min_s = max(0.05, float(motion.get("jog_min_ms", 300)) / 1000.0)

        # 開機先送 Disable Voltage，等使用者按激磁才走狀態機
        self.controlword = CMD_DISABLE_VOLTAGE
        self.target_position = 0
        self.target_velocity = 0
        self.op_mode = MODE_NO_MODE

        self.actual_position = 0
        self.actual_velocity = 0
        self.mode_display = 0
        self.statusword = 0
        self.err_code = 0
        self.expected_wkc = 0
        self._fault_latched = False

        self._lock = threading.Lock()
        self._pending = None
        self.job = None
        self.ramp = None

    def run(self):
        try:
            if not self._bringup():
                return
            self._cyclic()
        except Exception as exc:
            self._log(f"[-] 通訊執行緒例外: {exc}")
        finally:
            self._release()

    def stop(self):
        self.running = False
        self.wait(8000)

    def request_fault_reset(self):
        self._post("fault_reset")

    def request_shutdown(self):
        self._post("shutdown")

    def request_servo_on(self):
        """激磁。模式先維持 0，等 Operation Enabled 之前寫入，避免凍結成別的模式。"""
        self._post("servo_on")

    def request_homing(self):
        self._post("homing")

    def request_csp(self):
        self._post("csp")

    def request_quick_stop(self):
        self._post("quick_stop")

    def request_jog(self, step: int):
        self._post("jog", int(step))

    def request_move_to(self, position: int):
        self._post("move", int(position))

    def _post(self, name, arg=None):
        with self._lock:
            self._pending = (name, arg)

    def _take_pending(self):
        with self._lock:
            req = self._pending
            self._pending = None
        return req

    def _log(self, text: str):
        if text:
            self.log_message.emit(text)

    def _bringup(self) -> bool:
        self.master = pysoem.Master()
        try:
            self.master.open(self.ifname)
        except Exception as exc:
            self._log(f"[-] 無法開啟網卡 {self.ifname}: {exc}")
            self._log(self._adapter_hint())
            self.master = None
            return False

        if self.master.config_init() <= 0:
            self._log(f"[-] 在 {self.ifname} 上未發現從站")
            self._log(self._adapter_hint())
            return False

        self.slave = self.master.slaves[0]
        name = self.slave.name
        if isinstance(name, bytes):
            name = name.decode("utf-8", errors="replace")
        self._log(f"[*] 找到從站: {name}（共 {len(self.master.slaves)} 站）")

        # SDO 必須在 PREOP 寫。0x1C32:02 在這份 ESI 是唯讀，週期改寫 :01 與 :10
        self.master.state = pysoem.PREOP_STATE
        self.master.write_state()
        self.master.state_check(pysoem.PREOP_STATE, 500_000)

        self.master.config_map()
        out_size = len(self.slave.output)
        in_size = len(self.slave.input)
        self.expected_wkc = int(getattr(self.master, "expected_wkc", 0) or 0)
        if self.expected_wkc <= 0:
            self.expected_wkc = (1 if out_size else 0) + (2 if in_size else 0)
        if out_size != PDO_SIZE or in_size != PDO_SIZE:
            self._log(
                f"[-] PDO 長度是 Rx={out_size} / Tx={in_size}，這套上位機只對應預設的 "
                f"0x1600/0x1A00（{PDO_SIZE} bytes）。請確認 EEPROM 的 SM 指定沒有改成 0x1601/0x1A01"
            )
            return False
        self._log(f"[*] PDO 映射確認 {PDO_SIZE} bytes，預期 WKC={self.expected_wkc}")

        dc_ok = False
        try:
            self.master.config_dc()
            self.slave.dc_sync(True, self.cycle_ns, 0)
            dc_ok = True
            self._log(f"[*] 已啟用 DC SYNC0，週期 {self.cycle_ns} ns")
        except Exception as exc:
            self._log(f"[!] DC 設定失敗，維持從站目前的同步模式: {exc}")

        if dc_ok:
            # 0x02 = DC SYNC0。只在 PREOP 可寫，失敗只記錄，ESC 暫存器已由 dc_sync 寫過
            self._sdo_u16(0x1C32, 1, 0x0002, "SM2 同步型別")
            self._sdo_u16(0x1C33, 1, 0x0002, "SM3 同步型別")
            self._sdo_u32(0x1C32, 10, self.cycle_ns, "Sync0 週期")

        # 韌體尋原點用 0x6099:02；預設只有 10，這裡在進 SAFE_OP 前寫好
        self._sdo_u32(0x6099, 1, self.homing_speed_switch, "尋原點搜尋速度")
        self._sdo_u32(0x6099, 2, self.homing_speed_zero, "尋原點尋零速度")

        self.slave.output = PdoHandler.pack_rx(0, 0, 0, 0)

        self._log("[*] 交換 Process Data 並等待 SAFE_OP（輸出 SM 看門狗已啟用）")
        self.master.state = pysoem.SAFEOP_STATE
        self.slave.state = pysoem.SAFEOP_STATE
        self.master.write_state()
        if not self._wait_al("SAFE_OP", pysoem.SAFEOP_STATE, self.safeop_timeout_s):
            return False

        self._log("[*] 持續交換 Process Data 並等待 OP")
        self.master.state = pysoem.OP_STATE
        self.slave.state = pysoem.OP_STATE
        self.master.write_state()
        if not self._wait_al("OP", pysoem.OP_STATE, self.op_timeout_s):
            return False

        self._log(f"[+] 從站 {name} 已進入 OP")
        return True

    def _wait_al(self, label: str, target_state: int, timeout_s: float) -> bool:
        deadline = time.perf_counter() + timeout_s
        while self.running and time.perf_counter() < deadline:
            self._exchange()
            if self.master.state_check(target_state, 0) == target_state:
                self.master.read_state()
                return True
        self.master.read_state()
        al = int(getattr(self.slave, "al_status", 0) or 0)
        reason = _AL_STATUS_TEXT.get(al, "")
        extra = f"（{reason}）" if reason else ""
        self._log(f"[-] 無法進入 {label}，AL Status=0x{al:04X}{extra}")
        return False

    def _exchange(self):
        """送收一筆 PDO，回傳 (WKC, 匯流排往返毫秒)。sleep 不計入往返時間。"""
        t0 = time.perf_counter()
        self.slave.output = PdoHandler.pack_rx(
            self.controlword, self.target_position, self.target_velocity, self.op_mode
        )
        self.master.send_processdata()
        wkc = self.master.receive_processdata(self.recv_timeout_us)
        rtt_ms = (time.perf_counter() - t0) * 1000.0
        remain = self.cycle_s - (time.perf_counter() - t0)
        if remain > 0:
            time.sleep(remain)
        return int(wkc), rtt_ms

    def _cyclic(self):
        frame_count = 0
        lost_count = 0
        link_ok = True
        last_tick = time.perf_counter()
        last_ui = 0.0
        cycle_ms = self.cycle_s * 1000.0
        rtt_ms = 0.0

        while self.running:
            pending = self._take_pending()
            if pending is not None:
                self._start_job(pending[0], pending[1])

            t0 = time.perf_counter()
            cycle_ms = (t0 - last_tick) * 1000.0
            last_tick = t0
            wkc, rtt_ms = self._exchange()
            frame_count += 1

            tx = None
            if wkc == self.expected_wkc:
                try:
                    tx = PdoHandler.unpack_tx(self.slave.input)
                except ValueError as exc:
                    tx = None
                    self._log(f"[!] {exc}")
            if tx is None:
                lost_count += 1
                if link_ok:
                    link_ok = False
                    self._log(f"[!] WKC={wkc}，預期 {self.expected_wkc}，Process Data 中斷")
            else:
                if not link_ok:
                    link_ok = True
                    self._log("[+] Process Data 恢復")
                self.statusword = tx["statusword"]
                self.actual_position = tx["actual_position"]
                self.actual_velocity = tx["actual_velocity"]
                self.mode_display = tx["op_mode_display"]
                self._read_fault_once()
                self._step_job()
                self._step_ramp()

            now = time.perf_counter()
            if now - last_ui >= 0.05:
                last_ui = now
                self.telemetry.emit({
                    "link_ok": tx is not None,
                    "statusword": self.statusword,
                    "actual_position": self.actual_position,
                    "actual_velocity": self.actual_velocity,
                    "op_mode_display": self.mode_display,
                    "wkc": wkc,
                    "expected_wkc": self.expected_wkc,
                    "err_code": self.err_code,
                    "frame_count": frame_count,
                    "lost_count": lost_count,
                    "rtt_ms": rtt_ms,
                    "cycle_ms": cycle_ms,
                    "flow": self._flow_text(),
                })

    def _flow_text(self) -> str:
        if self.ramp is not None:
            return "CSP 插補中"
        if self.job is None:
            return "閒置"
        names = {
            "fault_reset": "故障復位",
            "shutdown": "Shutdown",
            "servo_on": "激磁",
            "homing": "尋原點",
            "csp": "切入 CSP",
            "quick_stop": "急停",
            "jog": "點動",
            "move": "位置移動",
        }
        return names.get(self.job["name"], self.job["name"])

    def _start_job(self, name, arg):
        self.ramp = None
        self.job = {
            "name": name,
            "phase": "enter",
            "t0": time.perf_counter(),
            "retries": 0,
            "seen_clear": False,
            "then": None,
            "arg": arg,
        }
        self._log(f"[Cmd] 開始：{self._flow_text()}")

    def _goto(self, phase: str):
        self.job["phase"] = phase
        self.job["t0"] = time.perf_counter()

    def _elapsed(self) -> float:
        return time.perf_counter() - self.job["t0"]

    def _fail(self, text: str):
        self._log(
            f"[-] {text}（Statusword=0x{self.statusword:04X}，"
            f"模式顯示={self.mode_display}，控制字=0x{self.controlword:04X}）"
        )
        self.job = None
        self.ramp = None

    def _ok(self, text: str):
        self._log(f"[+] {text}")
        self.job = None

    def _state(self) -> str:
        return classify_cia402_state(self.statusword)

    def _leave_enabled(self) -> bool:
        """還不能改 0x6060 時，先把軸退出 Operation Enabled / Quick Stop。"""
        state = self._state()
        if mode_change_allowed(state):
            return True
        if state == "QUICK_STOP":
            # 韌體的 Quick Stop 只接受 Disable Voltage (0x0000) 回到 Switch on Disabled
            self.controlword = CMD_DISABLE_VOLTAGE
        else:
            # 0x605B 預設是 0（立刻關閉）。0x0007 會卡在 Disable Operation pending，不要用
            self.controlword = CMD_SHUTDOWN
            self.target_position = self.actual_position
        return False

    def _begin_fault_reset(self, then_phase: str):
        self.job["then"] = then_phase
        self.job["retries"] = 0
        self.controlword = CMD_DISABLE_VOLTAGE
        self._goto("fr_zero")

    def _step_fault_reset(self) -> bool:
        """上一筆控制字必須是 0，這一筆才是 0x0080，MC_FaultReset() 才會執行。"""
        phase = self.job["phase"]
        if phase == "fr_zero":
            self.controlword = CMD_DISABLE_VOLTAGE
            if self._elapsed() >= 0.04:
                self.controlword = CMD_FAULT_RESET
                self._goto("fr_pulse")
            return True
        if phase == "fr_pulse":
            self.controlword = CMD_FAULT_RESET
            if self._elapsed() >= 0.06:
                self.controlword = CMD_DISABLE_VOLTAGE
                self._goto("fr_release")
            return True
        if phase == "fr_release":
            self.controlword = CMD_DISABLE_VOLTAGE
            if self._state() != "FAULT" and self._elapsed() >= 0.02:
                self._goto(self.job["then"])
                return True
            if self._elapsed() >= 0.08:
                self.job["retries"] += 1
                if self.job["retries"] >= 3:
                    self._fail("故障復位失敗。韌體要求先送 0x0000 再送 0x0080，目前重試 3 次仍在 Fault")
                    return True
                self.controlword = CMD_DISABLE_VOLTAGE
                self._goto("fr_zero")
            return True
        return False

    def _step_job(self):
        if self.job is None:
            return
        if self._step_fault_reset():
            return
        name = self.job["name"]
        if name == "fault_reset":
            self._job_fault_reset()
        elif name == "shutdown":
            self._job_shutdown()
        elif name == "servo_on":
            self._job_enable(MODE_NO_MODE, "激磁完成（模式 0，馬達軸已上電，尚未進 CSP）")
        elif name == "homing":
            self._job_homing()
        elif name == "csp":
            self._job_enable(MODE_CSP, "已進入 CSP。目標位置鎖在實際位置")
        elif name == "quick_stop":
            self._job_quick_stop()
        elif name == "jog":
            self._job_move(self.target_position + int(self.job["arg"]))
        elif name == "move":
            self._job_move(int(self.job["arg"]))

    def _job_fault_reset(self):
        if self.job["phase"] == "enter":
            if self._state() != "FAULT":
                self._ok("目前沒有 Fault，不需要復位")
                return
            self._begin_fault_reset("done")
            return
        if self.job["phase"] == "done":
            self._ok("故障復位完成，軸回到 Switch on Disabled")

    def _job_shutdown(self):
        phase = self.job["phase"]
        state = self._state()
        if phase == "enter":
            self._goto("wait")
            phase = "wait"
        if phase == "wait":
            if state == "FAULT":
                self._fail("目前在 Fault，請先按故障清除")
                return
            if state == "QUICK_STOP":
                self.controlword = CMD_DISABLE_VOLTAGE
            elif state in ("OP_ENABLED", "SWITCHED_ON"):
                self.controlword = CMD_SHUTDOWN
                self.target_position = self.actual_position
            if state in ("READY", "SWITCH_ON_DISABLED", "NOT_READY"):
                self.controlword = CMD_SHUTDOWN if state == "READY" else CMD_DISABLE_VOLTAGE
                self._ok("驅動已退出激磁")
                return
            if self._elapsed() > 3.0:
                self._fail("Shutdown 逾時")

    def _job_enable(self, mode: int, done_text: str, finish: bool = True) -> str:
        """把軸帶到指定模式的 Operation Enabled。

        模式只在離開激磁之後寫入，並在 Switched On 確認 0x6061 後才送 0x000F。
        finish=False 時留給尋原點接續，不結束整個工作。
        回傳 busy / ready / done。失敗時 job 會被清掉。
        """
        phase = self.job["phase"]
        state = self._state()
        if phase == "enter":
            if state == "FAULT":
                self._begin_fault_reset("drop")
                return "busy"
            self._goto("drop")
            return "busy"
        if phase in ("drop", "arm") and state == "FAULT":
            self._begin_fault_reset("drop")
            return "busy"
        if phase == "drop":
            if not self._leave_enabled():
                if self._elapsed() > 3.0:
                    self._fail("無法退出 Operation Enabled，因此不能改模式")
                return "busy"
            self.op_mode = mode
            self.target_position = self.actual_position
            self._goto("arm")
            return "busy"
        if phase == "arm":
            # 停在 Switched On，等 0x6061 跟上之後才送 Enable Operation
            if state in ("NOT_READY", "SWITCH_ON_DISABLED"):
                self.controlword = CMD_SHUTDOWN
            elif state == "QUICK_STOP":
                self.controlword = CMD_DISABLE_VOLTAGE
            elif state == "READY":
                self.controlword = CMD_SWITCH_ON
            elif state == "SWITCHED_ON" and self.mode_display == mode:
                self.target_position = self.actual_position
                self.controlword = CMD_ENABLE_OPERATION
                self._goto("enable")
                return "busy"
            elif state == "SWITCHED_ON":
                self.controlword = CMD_SWITCH_ON
            elif state == "OP_ENABLED":
                self._fail("模式還沒寫入就已經在 Operation Enabled，請先 Shutdown")
                return "busy"
            if self.job is not None and self._elapsed() > 5.0:
                self._fail(f"等待模式 {mode} 寫入 0x6061 逾時")
            return "busy"
        if phase == "enable":
            self.target_position = self.actual_position
            self.controlword = CMD_ENABLE_OPERATION
            if state == "OP_ENABLED" and self.mode_display == mode:
                self.target_position = self.actual_position
                if finish:
                    self._ok(done_text)
                    return "done"
                return "ready"
            if state == "OP_ENABLED" and self.mode_display != mode:
                self._fail(
                    f"已經 Operation Enabled，但 0x6061={self.mode_display}，不是 {mode}。"
                    "韌體在這個狀態不會再改模式"
                )
                return "busy"
            if state == "FAULT":
                self._fail(f"激磁過程進入 Fault，錯誤碼 0x{self.err_code:04X}")
                return "busy"
            if self._elapsed() > 10.0:
                self._fail("等待 Operation Enabled 逾時（向量對齊可能尚未完成）")
            return "busy"
        return "busy"

    def _job_homing(self):
        phase = self.job["phase"]
        if phase != "watch":
            # 尋原點的模式 6 必須在 Enable Operation 之前寫進 0x6061
            status = self._job_enable(MODE_HM, "", finish=False)
            if self.job is None or status != "ready":
                return
            self.job["seen_clear"] = False
            self.controlword = CMD_START_HOMING
            self._goto("watch")
            return

        self.controlword = CMD_START_HOMING
        sw = self.statusword
        if sw & SW_HOMING_ERROR or self._state() == "FAULT":
            self.controlword = CMD_ENABLE_OPERATION
            self._fail(f"尋原點失敗，錯誤碼 0x{self.err_code:04X}")
            return
        # 待機時 bit 12 就是 1。必須先看到它被清掉，才代表尋原點真的開始
        if not (sw & SW_HOMING_ATTAINED):
            self.job["seen_clear"] = True
        if self.job["seen_clear"] and (sw & SW_HOMING_ATTAINED) and (sw & SW_TARGET_REACHED):
            self.controlword = CMD_ENABLE_OPERATION
            self.target_position = self.actual_position
            self._ok(f"尋原點完成，位置={self.actual_position}。請再按 CSP，不要在激磁中直接改模式")
            return
        if not self.job["seen_clear"] and self._elapsed() > 3.0:
            self.controlword = CMD_ENABLE_OPERATION
            self._fail("尋原點沒有啟動。bit 12 在待機時就是 1，必須先看到它清掉")
            return
        if self._elapsed() > 20.0:
            self.controlword = CMD_ENABLE_OPERATION
            self._fail("尋原點逾時")

    def _job_quick_stop(self):
        phase = self.job["phase"]
        state = self._state()
        if phase == "enter":
            self.target_position = self.actual_position
            self.ramp = None
            if state in ("SWITCH_ON_DISABLED", "NOT_READY", "READY"):
                self.controlword = CMD_DISABLE_VOLTAGE
                self._ok("驅動本來就沒有激磁")
                return
            self.controlword = CMD_QUICK_STOP
            self._goto("wait_qs")
            return
        if phase == "wait_qs":
            self.controlword = CMD_QUICK_STOP
            if state in ("QUICK_STOP", "SWITCH_ON_DISABLED", "FAULT"):
                self.controlword = CMD_DISABLE_VOLTAGE
                self._goto("disable")
            elif self._elapsed() > 2.0:
                self._fail("急停沒有進入 Quick Stop")
            return
        if phase == "disable":
            self.controlword = CMD_DISABLE_VOLTAGE
            if state in ("SWITCH_ON_DISABLED", "NOT_READY", "READY", "FAULT"):
                if state == "FAULT":
                    self._fail(f"急停後進入 Fault，錯誤碼 0x{self.err_code:04X}")
                else:
                    self._ok("急停完成，驅動已關閉。要再動之前請重新激磁")
                return
            if self._elapsed() > 2.0:
                self._fail("急停後無法以 0x0000 離開 Quick Stop")

    def _job_move(self, end_pos: int):
        if self._state() != "OP_ENABLED" or self.mode_display != MODE_CSP:
            self._fail("點動只允許在 CSP 且 Operation Enabled 時下達。請先按「啟動位置」")
            return
        start = self.target_position
        delta = end_pos - start
        if delta == 0:
            self._ok(f"目標位置已是 {end_pos}")
            return
        duration = max(self.jog_min_s, abs(delta) / float(self.jog_speed))
        self.ramp = {
            "start": start,
            "end": end_pos,
            "t0": time.perf_counter(),
            "dur": duration,
        }
        self.job = None
        self._log(f"[CSP] 由 {start} 插補到 {end_pos}，時間 {duration:.2f} s")

    def _step_ramp(self):
        if self.ramp is None or self.job is not None:
            return
        if self._state() != "OP_ENABLED":
            self.ramp = None
            self.target_position = self.actual_position
            self._log("[!] 激磁中斷，已取消插補並把目標鎖回實際位置")
            return
        ramp = self.ramp
        ratio = (time.perf_counter() - ramp["t0"]) / ramp["dur"]
        if ratio >= 1.0:
            self.target_position = ramp["end"]
            self.ramp = None
            self._log(f"[+] 到達目標位置 {ramp['end']}")
            return
        # 五次平滑，起點與終點速度為 0，避免 CSP 一個週期吃下整段位移
        smooth = ratio ** 3 * (ratio * (ratio * 6.0 - 15.0) + 10.0)
        self.target_position = int(round(ramp["start"] + (ramp["end"] - ramp["start"]) * smooth))

    def _read_fault_once(self):
        fault = self._state() == "FAULT"
        if not fault:
            self._fault_latched = False
            self.err_code = 0
            return
        if self._fault_latched:
            return
        self._fault_latched = True
        try:
            raw = self.slave.sdo_read(0x603F, 0)
            self.err_code = struct.unpack_from("<H", raw)[0]
            self._log(f"[!] Fault，0x603F=0x{self.err_code:04X}")
        except Exception as exc:
            self.err_code = 0xFFFF
            self._log(f"[!] Fault，但讀取 0x603F 失敗: {exc}")

    def _sdo_u16(self, index: int, sub: int, value: int, label: str):
        self._sdo(index, sub, struct.pack("<H", value & 0xFFFF), label)

    def _sdo_u32(self, index: int, sub: int, value: int, label: str):
        self._sdo(index, sub, struct.pack("<I", value & 0xFFFFFFFF), label)

    def _sdo(self, index: int, sub: int, payload: bytes, label: str):
        try:
            self.slave.sdo_write(index, sub, payload)
            self._log(f"[*] SDO 0x{index:04X}:{sub:02d} {label} 已寫入")
        except Exception as exc:
            self._log(f"[!] SDO 0x{index:04X}:{sub:02d} {label} 寫入失敗: {exc}")

    def _adapter_hint(self) -> str:
        try:
            adapters = pysoem.find_adapters()
        except Exception as exc:
            return f"[!] 無法列舉網卡: {exc}"
        if not adapters:
            return "[!] 找不到可用網卡"
        lines = ["[*] 這台電腦看得到的介面："]
        for adapter in adapters:
            desc = getattr(adapter, "desc", "") or ""
            lines.append(f"    {adapter.name}  {desc}")
        lines.append("[*] 請把 config/settings.yaml 的 ethercat.ifname 改成上面其中一個名稱")
        return "\n".join(lines)

    def _release(self):
        if self.master is None or self.slave is None:
            if self.master is not None:
                try:
                    self.master.close()
                except Exception:
                    pass
            return
        self.controlword = CMD_DISABLE_VOLTAGE
        self.op_mode = MODE_NO_MODE
        self.target_velocity = 0
        for _ in range(5):
            try:
                self.slave.output = PdoHandler.pack_rx(self.controlword, self.actual_position, 0, 0)
                self.master.send_processdata()
                self.master.receive_processdata(self.recv_timeout_us)
            except Exception:
                break
            time.sleep(self.cycle_s)
        try:
            self.master.close()
        except Exception:
            pass
        self._log("[*] EtherCAT 主站已關閉，控制字已送出 0x0000")
