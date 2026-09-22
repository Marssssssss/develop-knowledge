"""Linux crc32()：LSB-first、多项式 0xEDB88320、首尾均不取反。

见 include/linux/crc32.h 的 "does not invert the CRC at the beginning or end"。
"""

def _make_table():
    tbl = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ (0xEDB88320 if (c & 1) else 0)
        tbl.append(c)
    return tbl


_TABLE = _make_table()


def crc32(data, seed=0):
    crc = seed & 0xFFFFFFFF
    for b in data:
        crc = _TABLE[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return crc & 0xFFFFFFFF


