"""btrfs send stream 的 CRC32C 校验与增量流。

事实来源（实读 btrfs 文档「Send/receive」）：

  * send 遍历一个**只读** subvolume，full 模式产出完整表示；给了参考 subvolume 则
    产出相对它的增量（incremental）
  * receive 重建出来的 subvolume **不是 1:1**：inode 号、subvolume UUID 都可能不同
  * 流是一串编码命令（改元数据、创建/克隆/截断数据 extent、重命名/删除）
  * **每条命令带 CRC32C 校验和，初值为 0，且不取反**（这是最容易写错的地方）

因此这里实现 CRC32C 时把 init / xorout 做成参数，好把"标准 CRC-32C"与
"btrfs 变体"放在同一个函数里对照。
"""

POLY = 0x82F63B78  # CRC-32C (Castagnoli) 反射形式的多项式


def _make_table():
    tbl = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ (POLY if c & 1 else 0)
        tbl.append(c)
    return tbl


TABLE = _make_table()


def crc32c_bitwise(data, init=0xFFFFFFFF, xorout=True):
    """逐位移位的参考实现（慢，用来交叉验证表驱动版本）。"""
    crc = init
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (POLY if crc & 1 else 0)
    return (crc ^ 0xFFFFFFFF) if xorout else crc


def crc32c(data, init=0xFFFFFFFF, xorout=True):
    crc = init
    for byte in data:
        crc = TABLE[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return (crc ^ 0xFFFFFFFF) if xorout else crc


def btrfs_csum(data):
    """btrfs send stream 用的 CRC32C：初值 0，不取反。"""
    return crc32c(data, init=0, xorout=False)


class SendStream(object):
    """一串带校验的命令。每条命令 = (cmd, payload)。"""

    def __init__(self):
        self.cmds = []

    def emit(self, cmd, payload=b""):
        self.cmds.append((cmd, payload, btrfs_csum(payload)))
        return self

    def verify(self):
        """逐条重算校验和，返回失败的序号列表。"""
        bad = []
        for i, (cmd, payload, csum) in enumerate(self.cmds):
            if btrfs_csum(payload) != csum:
                bad.append((i, cmd))
        return bad

    def size(self):
        return sum(len(p) for _, p, _ in self.cmds)


def full_send(files):
    """full 模式：起始于空 subvolume，逐个创建文件。"""
    s = SendStream()
    s.emit("mkfile", b"")
    for name, blocks in files:
        s.emit("create", name.encode())
        s.emit("write", name.encode() + b":" + str(blocks).encode())
    s.emit("set_readonly", b"")
    return s


def incremental_send(base, new):
    """incremental 模式：只发相对 base 的差异。

    返回 (stream, 命令数)；base 里已存在且内容一致的文件直接跳过。
    """
    s = SendStream()
    base_map = dict(base)
    changed = 0
    for name, blocks in new:
        if base_map.get(name) == blocks:
            continue
        if name in base_map:
            s.emit("update", name.encode())
        else:
            s.emit("create", name.encode())
        s.emit("write", name.encode() + b":" + str(blocks).encode())
        changed += 1
    return s, changed
