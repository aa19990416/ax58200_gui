import struct

class PdoHandler:
    # 根據 0x1600: Controlword(u16), TargetPos(i32), TargetVel(i32), OpMode(i8), Padding(3B) -> 14 Bytes
    RX_FORMAT = "<HiiB3s"
    # 根據 0x1A00: Statusword(u16), ActualPos(i32), ActualVel(i32), OpModeDisp(i8), Padding(3B) -> 14 Bytes
    TX_FORMAT = "<HiiB3s"

    @classmethod
    def pack_rx(cls, controlword: int, target_pos: int, target_vel: int, op_mode: int) -> bytes:
        return struct.pack(cls.RX_FORMAT, controlword, target_pos, target_vel, op_mode, b'\x00\x00\x00')

    @classmethod
    def unpack_tx(cls, raw_bytes: bytes) -> dict:
        statusword, act_pos, act_vel, mode_disp, _ = struct.unpack(cls.TX_FORMAT, raw_bytes)
        return {
            "statusword": statusword,
            "actual_position": act_pos,
            "actual_velocity": act_vel,
            "op_mode_display": mode_disp
        }