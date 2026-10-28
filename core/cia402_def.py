# 運行模式 Modes of Operation (0x6060)
MODE_NO_MODE = 0
MODE_PP      = 1
MODE_PV      = 3
MODE_HM      = 6   # Homing Mode (回原點模式)
MODE_CSP     = 8   # Cyclic Synchronous Position
MODE_CSV     = 9   # Cyclic Synchronous Velocity

# CiA 402 控制字指令 (Controlword 0x6040)
CMD_FAULT_RESET      = 0x0080
CMD_SHUTDOWN         = 0x0006
CMD_SWITCH_ON        = 0x0007
CMD_ENABLE_OPERATION = 0x000F
CMD_START_HOMING     = 0x001F  # Enable Operation (0x0F) + Start Homing (bit 4)
CMD_QUICK_STOP       = 0x0002

def parse_cia402_state(statusword: int) -> tuple:
    """解析 CiA 402 Statusword (回傳: 描述文字, 顏色代碼)"""
    # 優先判定硬體故障 (Bit 3 為 1)
    if statusword & 0x0008:
        return ("Fault (故障報警)", "red")
    
    # 判定急停
    if (statusword & 0x006F) == 0x0007:
        return ("Quick Stop Active (急停動作中)", "#c0392b")

    masked_basic = statusword & 0x004F
    masked_adv = statusword & 0x006F

    if masked_adv == 0x0027:
        return ("Operation Enabled (激磁運行中)", "green")
    elif masked_adv == 0x0023:
        return ("Switched On (已上電就緒)", "#3498db")
    elif masked_adv == 0x0021:
        return ("Ready to Switch On (準備完成)", "#2980b9")
    elif masked_basic == 0x0040:
        return ("Switch on Disabled (待命中)", "orange")
    elif masked_basic == 0x0000:
        return ("Not Ready to Switch On (初始化中)", "gray")

    return (f"狀態過渡中 (0x{statusword:04X})", "gray")