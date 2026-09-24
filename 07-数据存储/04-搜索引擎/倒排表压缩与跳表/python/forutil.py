"""Lucene ForUtil：定长位打包（BLOCK_SIZE=256）。

忠实转写自 apache/lucene@main
``core/src/java/org/apache/lucene/codecs/lucene104/ForUtil.java``。

要点：
  * ``collapse8/collapse16`` 先把 256 个值交叠进 64/128 个「值字」，每个值字的
    32 位切成 32/primitiveSize 条泳道；
  * 位打包在每条泳道的 primitiveSize 位字段里以完全相同的方式进行 —— 这正是
    MASKS8/16 要用 expandMask 摊成 0x01010101 的原因；
  * 输出字节是 bpv*8 个 32 位大端字，故 bpv=8 时落盘顺序是 0,64,128,192,1,...
    这种「泳道转置」而不是朴素拼接。
"""

from bitio import MASK32, _i32

BLOCK_SIZE = 256
BLOCK_SIZE_LOG2 = 8

def expand_mask16(m):
    return _i32(m | (m << 16))


def expand_mask8(m):
    return expand_mask16(m | (m << 8))


def mask32(bpv):
    return (1 << bpv) - 1


def mask16(bpv):
    return expand_mask16((1 << bpv) - 1)


def mask8(bpv):
    return expand_mask8((1 << bpv) - 1)


MASKS8 = [mask8(i) for i in range(8)]
MASKS16 = [mask16(i) for i in range(16)]
MASKS32 = [mask32(i) for i in range(32)]


def collapse8(arr):
    """4 个值塞进 1 个 int（每值 8 位，大端在前）。原地修改。"""
    for i in range(64):
        arr[i] = _i32((arr[i] << 24) | (arr[64 + i] << 16)
                      | (arr[128 + i] << 8) | arr[192 + i])


def expand8(arr):
    """collapse8 的逆。原地修改。"""
    for i in range(64):
        b = arr[i]
        arr[i] = (b >> 24) & 0xFF
        arr[64 + i] = (b >> 16) & 0xFF
        arr[128 + i] = (b >> 8) & 0xFF
        arr[192 + i] = b & 0xFF


def collapse16(arr):
    """2 个值塞进 1 个 int（每值 16 位）。原地修改。"""
    for i in range(128):
        arr[i] = _i32((arr[i] << 16) | arr[128 + i])


def expand16(arr):
    """collapse16 的逆。原地修改。"""
    for i in range(128):
        l = arr[i]
        arr[i] = (l >> 16) & 0xFFFF
        arr[128 + i] = l & 0xFFFF


def num_bytes(bpv):
    """256 个 bpv 位的值占多少字节：bpv << (BLOCK_SIZE_LOG2 - 3)。"""
    return bpv << (BLOCK_SIZE_LOG2 - 3)


def primitive_size_of(bpv):
    if bpv <= 8:
        return 8
    if bpv <= 16:
        return 16
    return 32


def _masks_for(primitive_size):
    return {8: MASKS8, 16: MASKS16, 32: MASKS32}[primitive_size]


def placement_map(bpv, primitive_size=None):
    """复刻 ForUtil.encode 的控制流，产出放置图。

    源码把 256 个值先交叠进 ``256*primitiveSize/32`` 个「值字」，每个值字的
    32 位又被切成 ``32/primitiveSize`` 条泳道；位打包在每条泳道的
    ``primitiveSize`` 位字段里以完全相同的方式进行（这正是 MASKS8/16 要
    expand 成 0x01010101 的原因）。

    返回 ``pl[i][k] = (word, field_bit)``：第 i 个值字的第 k 位（k=0 为最低位）
    落在第 word 个输出字的第 ``lane*primitiveSize + field_bit`` 位。
    encode 与 decode 共用这张图，天然互逆。
    """
    ps = primitive_size if primitive_size is not None else primitive_size_of(bpv)
    num_ints = BLOCK_SIZE * ps // 32
    num_ints_per_shift = bpv * 8
    pl = [[None] * bpv for _ in range(num_ints)]

    def take(idx, start, nbits, word, low):
        for k in range(nbits):
            pl[idx][start + k] = (word, low + k)

    idx = 0
    shift = ps - bpv
    for i in range(num_ints_per_shift):
        take(idx, 0, bpv, i, shift)
        idx += 1
    shift -= bpv
    while shift >= 0:
        for i in range(num_ints_per_shift):
            take(idx, 0, bpv, i, shift)
            idx += 1
        shift -= bpv

    remaining_bits_per_int = shift + bpv
    bits_left = bpv
    placed = 0
    tmp_idx = 0
    while idx < num_ints and remaining_bits_per_int > 0:
        if bits_left >= remaining_bits_per_int:
            bits_left -= remaining_bits_per_int
            take(idx, placed, remaining_bits_per_int, tmp_idx, 0)
            placed += remaining_bits_per_int
            tmp_idx += 1
            if bits_left == 0:
                idx += 1
                placed = 0
                bits_left = bpv
        else:
            consumed = bits_left
            take(idx, placed, consumed, tmp_idx,
                 remaining_bits_per_int - consumed)
            idx += 1
            bits_left = bpv - remaining_bits_per_int + consumed
            placed = 0
            take(idx, 0, remaining_bits_per_int - consumed, tmp_idx, 0)
            placed = remaining_bits_per_int - consumed
            tmp_idx += 1
    for i in range(num_ints):
        assert all(x is not None for x in pl[i]), (bpv, i)
    return pl


def forutil_encode(ints, bpv):
    """把 256 个值按 bpv 位打包，返回待写的 int 列表（每个按大端写 4 字节）。"""
    work = list(ints)
    primitive_size = primitive_size_of(bpv)
    if primitive_size == 8:
        collapse8(work)
    elif primitive_size == 16:
        collapse16(work)
    return forutil_encode_raw(work, bpv, primitive_size)


def forutil_encode_raw(work, bpv, primitive_size):
    """ForUtil.encode 的按位转写（等价于源码的移位加掩码写法）。"""
    pl = placement_map(bpv, primitive_size)
    lanes = 32 // primitive_size
    tmp = [0] * (bpv * 8)
    for i, spots in enumerate(pl):
        for k, (word, fb) in enumerate(spots):
            for j in range(lanes):
                b = (work[i] >> (j * primitive_size + k)) & 1
                tmp[word] = _i32(tmp[word] | (b << (j * primitive_size + fb)))
    return tmp


def forutil_decode(words, bpv):
    """forutil_encode 的逆：按同一张放置图读回 256 个值。"""
    primitive_size = primitive_size_of(bpv)
    pl = placement_map(bpv, primitive_size)
    if len(words) != bpv * 8:
        raise ValueError("需要 %d 个字，实得 %d" % (bpv * 8, len(words)))
    lanes = 32 // primitive_size
    out = [0] * len(pl)
    for i, spots in enumerate(pl):
        v = 0
        for k, (word, fb) in enumerate(spots):
            b = (words[word] >> fb) & 1  # 取 0 号泳道即可还原一个值
            v |= b << k
            for j in range(1, lanes):
                bj = (words[word] >> (j * primitive_size + fb)) & 1
                v |= bj << (j * primitive_size + k)
        out[i] = v
    if primitive_size == 32:
        return out
    buf = out + [0] * (BLOCK_SIZE - len(out))
    if primitive_size == 8:
        expand8(buf)
    else:
        expand16(buf)
    return buf


def forutil_encode_bytes(ints, bpv):
    """打包并返回落盘字节串。"""
    words = forutil_encode(ints, bpv)
    buf = bytearray()
    for w in words:
        buf += w.to_bytes(4, "big")
    assert len(buf) == num_bytes(bpv)
    return bytes(buf)
