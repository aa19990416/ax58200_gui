import struct

# ESI 預設 SM 指定 0x1600 / 0x1A00：
# Controlword(u16) + Position(i32) + Velocity(i32) + Mode(i8) + 3 byte padding = 14
PDO_SIZE = 14
_PDO_PREFIX = "<Hiib"


class PdoHandler:
    @classmethod
    def pack_rx(cls, controlword: int, target_pos: int, target_vel: int, op_mode: int) -> bytes:
        # 3x 補滿 ESI 的 24 bit padding，模式用有號 8 bit（0x6060 是 SINT）
        return struct.pack(_PDO_PREFIX + "3x", controlword & 0xFFFF, int(target_pos), int(target_vel), int(op_mode))

    @classmethod
    def unpack_tx(cls, raw_bytes: bytes) -> dict:
        if raw_bytes is None or len(raw_bytes) < PDO_SIZE:
            raise ValueError(f"TxPDO 長度為 {0 if raw_bytes is None else len(raw_bytes)}，預期 {PDO_SIZE}")
        statusword, act_pos, act_vel, mode_disp = struct.unpack_from(_PDO_PREFIX, raw_bytes)
        return {
            "statusword": statusword,
            "actual_position": act_pos,
            "actual_velocity": act_vel,
            "op_mode_display": mode_disp,
        }
