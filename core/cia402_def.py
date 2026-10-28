# 運行模式 Modes of Operation (0x6060)
MODE_NO_MODE = 0
MODE_PP      = 1
MODE_PV      = 3
MODE_HM      = 6   # Homing Mode (回原點模式)
MODE_CSP     = 8   # Cyclic Synchronous Position
MODE_CSV     = 9   # Cyclic Synchronous Velocity

# CiA 402 控制字指令 (Controlword 0x6040)
CMD_FAULT_RESET      = 0x0080
CMD_DISABLE_VOLTAGE  = 0x0000
CMD_QUICK_STOP       = 0x0002
CMD_SHUTDOWN         = 0x0006
CMD_SWITCH_ON        = 0x0007
CMD_ENABLE_OPERATION = 0x000F
CMD_START_HOMING     = 0x001F  # Enable Operation (0x0F) + Homing start (bit 4)

# Statusword 在 Homing 模式的製造商／模式相關位元
SW_FAULT         = 0x0008
SW_TARGET_REACHED = 0x0400
SW_HOMING_ATTAINED = 0x1000
SW_HOMING_ERROR  = 0x2000


def classify_cia402_state(statusword: int) -> str:
    """把 Statusword 收成狀態機用的代號。遮罩與 AX58200 CiA402 實作一致。"""
    if statusword & SW_FAULT:
        return "FAULT"
    masked = statusword & 0x006F
    if masked == 0x0007:
        return "QUICK_STOP"
    if masked == 0x0027:
        return "OP_ENABLED"
    if masked == 0x0023:
        return "SWITCHED_ON"
    if masked == 0x0021:
        return "READY"
    if (statusword & 0x004F) == 0x0040:
        return "SWITCH_ON_DISABLED"
    if (statusword & 0x004F) == 0x0000:
        return "NOT_READY"
    return "TRANSIT"


def mode_change_allowed(state: str) -> bool:
    """韌體只在 bConfigurationAllowed 為真時才把 0x6060 抄到 0x6061。

    Operation Enabled、Quick Stop、Fault Reaction 期間改模式會被凍結。
    """
    return state in ("NOT_READY", "SWITCH_ON_DISABLED", "READY", "SWITCHED_ON", "FAULT")


def parse_cia402_state(statusword: int) -> tuple:
    """解析 CiA 402 Statusword (回傳: 描述文字, 顏色代碼)"""
    state = classify_cia402_state(statusword)
    table = {
        "FAULT": ("Fault (故障報警)", "red"),
        "QUICK_STOP": ("Quick Stop Active (急停動作中)", "#c0392b"),
        "OP_ENABLED": ("Operation Enabled (激磁運行中)", "green"),
        "SWITCHED_ON": ("Switched On (已上電就緒)", "#3498db"),
        "READY": ("Ready to Switch On (準備完成)", "#2980b9"),
        "SWITCH_ON_DISABLED": ("Switch on Disabled (待命中)", "orange"),
        "NOT_READY": ("Not Ready to Switch On (初始化中)", "gray"),
    }
    if state in table:
        return table[state]
    return (f"狀態過渡中 (0x{statusword:04X})", "gray")
