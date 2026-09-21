"""CRC 的参数化模型（Rocksoft / RevEng 记法）与官方 check / residue 校验。

一个 CRC 算法由 6 个参数决定：`width, poly, init, refin, refout, xorout`。
RevEng 目录对每种模型给出两个黄金值：

- `check`   = 对 ASCII "123456789"（9 字节）算出的 CRC；
- `residue` = 把 `check` 追加到该消息**之后**再算一次 CRC 得到的寄存器值
              （无 xorout 时是那个常数；有 xorout 时同样带上 xorout）。

`residue` 是判定「参数猜对了没」的强力不变量：它与 init 无关，只由 poly 决定。
"""

from catalogue import CATALOGUE


def reflect(v, width):
    """按 width 位反转比特序。"""
    r = 0
    for i in range(width):
        if (v >> i) & 1:
            r |= 1 << (width - 1 - i)
    return r


class Model:
    def __init__(self, width, poly, init, refin, refout, xorout, name=""):
        self.width = width
        self.poly = poly
        self.init = init
        self.refin = refin
        self.refout = refout
        self.xorout = xorout
        self.name = name

    def __repr__(self):
        return ("Model(%s width=%d poly=0x%x init=0x%x refin=%s refout=%s "
                "xorout=0x%x)" % (self.name, self.width, self.poly, self.init,
                                  self.refin, self.refout, self.xorout))

    def __eq__(self, other):
        return isinstance(other, Model) and self.params() == other.params()

    def __hash__(self):
        return hash(self.params())

    def params(self):
        return (self.width, self.poly, self.init, self.refin, self.refout,
                self.xorout)


def from_row_raw(row):
    name, width, poly, init, refin, refout, xorout, _c, _r = row
    return Model(width, poly, init, refin, refout, xorout, name)


# ---------------------------------------------------------------- 核心计算

def crc(model, data, init=None):
    """按参数化模型逐字节计算 CRC。

    Rocksoft / RevEng 记法下**寄存器永远左移**：refin 只反射输入字节（与 init），
    refout 反射的是最终寄存器，poly 始终按 MSB-first 正规形使用。把 refin 理解成
    「寄存器改成右移」是最常见的误读 —— 那样 CRC-32 会算出 0xFC891918 而不是
    0xCBF43926。

    实现走位级定义式（消息位与移出位异或后决定是否异或多项式），因此 width 可以
    小于 8（CRC-3/GSM、CRC-4/G-704 等）；字节级 `reg ^= b << (width-8)` 在
    width < 8 时会直接崩。
    """
    width = model.width
    mask = (1 << width) - 1
    poly = model.poly & mask
    reg0 = model.init if init is None else init
    reg = (reflect(reg0, width) if model.refin else reg0) & mask
    for byte in data:
        b = byte & 0xFF
        if model.refin:
            b = reflect(b, 8)
        for i in range(7, -1, -1):          # 一律 MSB 先进
            bit = (b >> i) & 1
            out = ((reg >> (width - 1)) & 1) ^ bit
            reg = (reg << 1) & mask
            if out:
                reg ^= poly
    if model.refout:
        reg = reflect(reg, width)
    return (reg ^ model.xorout) & mask


CHECK_MSG = b"123456789"


def check_value(model):
    """官方 `check`：对 "123456789" 的 CRC。"""
    return crc(model, CHECK_MSG)


def append_check(model, msg, value):
    """把 CRC 值按模型的字节序追加到消息尾部。

    refin=True（右移寄存器）时 CRC 按**小端**追加；否则按大端追加。
    """
    n = (model.width + 7) // 8
    bs = value.to_bytes(n, "little" if model.refin else "big")
    return msg + bs


def residue(model):
    """官方 `residue`：message||check 再算一次 CRC。"""
    v = check_value(model)
    return crc(model, append_check(model, CHECK_MSG, v))


def functional_key(model, msgs=None, width_bytes=4):
    """函数等价类的指纹：在一批消息上的 CRC 输出序列。

    参数组不唯一（refin/refout 与 poly 的反射可以互相抵消），**函数**才唯一，
    所以断言只能落在指纹上，不能落在参数上。
    """
    if msgs is None:
        msgs = [b"", b"\x00", b"\xff", b"A", b"123456789",
                bytes(range(32)), b"\xde\xad\xbe\xef" * 3,
                bytes(range(256))]
    return tuple(crc(model, m) for m in msgs)


# 目录里有 4 个条目的 init 已经是「寄存器域」（即已反射过）的值，refin=True 时
# 不能再次反射，否则 check 对不上。这是 RevEng 目录已知的口径不一致，本 demo
# 如实记录而不是偷偷改数据。
INIT_ALREADY_REFLECTED = frozenset({
    "CRC-16/ISO-IEC-14443-3-A",  # init=0xC6C6，反射成 0x6363 反而错
    "CRC-16/RIELLO",             # init=0xB2AA
    "CRC-16/TMS37157",           # init=0x89EC
    "CRC-24/BLE",                # init=0x555555
})


def from_row(row, raw=False):
    """把目录行变成 Model。raw=False 时套用上面的 init 例外。"""
    m = from_row_raw(row)
    if not raw and m.refin and m.name in INIT_ALREADY_REFLECTED:
        m.init = reflect(m.init, m.width)
    return m


def catalogue_models():
    return [from_row(r) for r in CATALOGUE]


# ---------------------------------------------------------------- 快速路径

def _table(width, poly):
    """width >= 8 时的 256 项查表：tbl[b] = 从 0 出发吃掉字节 b 后的寄存器。"""
    mask = (1 << width) - 1
    top = 1 << (width - 1)
    tbl = []
    for b in range(256):
        reg = b << (width - 8)
        for _ in range(8):
            reg = ((reg << 1) ^ poly) & mask if (reg & top) else (reg << 1) & mask
        tbl.append(reg & mask)
    return tbl


def crc_fast(model, data, init=None):
    """字节级查表实现，只在 width >= 8 时可用（比位级快约 50 倍）。"""
    width = model.width
    if width < 8:
        return crc(model, data, init)
    mask = (1 << width) - 1
    tbl = _table(width, model.poly & mask)
    reg = (model.init if init is None else init) & mask
    if model.refin:
        reg = reflect(reg, width)
    for byte in data:
        b = reflect(byte & 0xFF, 8) if model.refin else (byte & 0xFF)
        idx = ((reg >> (width - 8)) ^ b) & 0xFF
        reg = ((reg << 8) ^ tbl[idx]) & mask
    if model.refout:
        reg = reflect(reg, width)
    return (reg ^ model.xorout) & mask
