"""struct linux_dirent64 与 getdents64 的装填模型（从 dcache.py 拆出）。

d_reclen = ALIGN(19 + len(name) + 1, 8)；装不下就停不截断；
只要发出过至少一条，内核就用写入字节数覆盖预设的 -EINVAL；
最后一条的 d_off 会被覆写成 ctx.pos（续读位置）。
"""

import struct

from dcache import DT_REG  # noqa: E402

EINVAL = -22

# struct linux_dirent64：d_ino(8) d_off(8) d_reclen(2) d_type(1) d_name[]
DIRENT64_HEADER = 8 + 8 + 2 + 1      # 19 字节（未对齐到 8）
DIRENT64_FMT = "<QQHB"               # 不含 d_name
# struct linux_dirent（老接口）：d_type 被塞在 d_reclen-1 那个原填充字节上
DIRENT_HEADER = 8 + 8 + 2

def dirent64_record(ino, off, name, dtype):
    """打包一条 linux_dirent64（d_name 以 NUL 结尾）。"""
    nb = name.encode() + b"\x00"
    reclen = DIRENT64_HEADER + len(nb)
    # d_reclen 必须对齐到 8 字节（最后一条除外也要对齐到缓冲末尾）
    pad = (-reclen) % 8
    body = struct.pack(DIRENT64_FMT, ino, off, reclen + pad, dtype) + nb
    return body + b"\x00" * pad, reclen + pad


def unpack_dirent64(buf, offset=0):
    """解出一条记录，返回 (ino, off, reclen, d_type, name, next_offset)。"""
    if offset + DIRENT64_HEADER > len(buf):
        return None
    ino, off, reclen, dtype = struct.unpack_from(DIRENT64_FMT, buf, offset)
    if reclen == 0 or offset + reclen > len(buf):
        return None
    raw = buf[offset + DIRENT64_HEADER:offset + reclen]
    name = raw.split(b"\x00", 1)[0].decode()
    return ino, off, reclen, dtype, name, offset + reclen




def emit_getdents(entries, buffer_size):
    """模拟一次 getdents64 调用（fs/readdir.c 的 filldir64 + SYSCALL）。

    entries: [(ino, name, dtype), ...]

    返回 (buf, 写入条数, 剩余条数, 返回值)。
    内核的两个关键行为：

    1. ``if (reclen > ctx->count) return false`` —— 装不下就停，**不截断**
    2. 只要发出过至少一条，最后就 ``error = count - buf.ctx.count``，
       把先前预设的 ``buf.error = -EINVAL`` **覆盖掉**。
       所以 EINVAL 只在**一条都装不下**时才对用户可见
       （man page 的 "EINVAL: Result buffer is too small"）。
    3. 最后一条的 d_off 会被覆写成 ``buf.ctx.pos``（下一次的续读位置），
       这正是 man page 说 d_off "对用户空间没有明确含义"的原因。
    """
    out = bytearray()
    count = buffer_size
    prev_reclen = 0
    n = 0
    for i, (ino, name, dtype) in enumerate(entries):
        rec, size = dirent64_record(ino, i + 1, name, dtype)
        if size > count:
            break
        out += rec
        count -= size
        prev_reclen = size
        n += 1
    if n == 0:
        return b"", 0, len(entries), EINVAL
    # 最后一条的 d_off 覆写成 ctx.pos（下一条的位置）
    struct.pack_into("<Q", out, len(out) - prev_reclen + 8, n + 1)
    return bytes(out), n, len(entries) - n, len(out)


def walk(buf):
    """遍历一次 getdents64 返回的缓冲区。"""
    off, res = 0, []
    while True:
        r = unpack_dirent64(buf, off)
        if r is None:
            break
        res.append(r)
        off = r[5]
    return res


def dirent64_size(name):
    """名字长度 n 的记录占多少字节（含 NUL 与 8 字节对齐填充）。"""
    rec, size = dirent64_record(1, 1, name, DT_REG)
    return size
