"""Vitess Primary Vindex: 列值 -> keyspace ID。

转写自 `vitessio/vitess/go/vt/vtgate/vindexes/` 下四个 vindex 的 `Hash` 方法:

| vindex        | Hash 实现                                    | 源码文件                | Cost |
| ------------- | -------------------------------------------- | ----------------------- | ---- |
| `binary`      | 原样返回字节串 `id.ToBytes()`                 | `binary.go`             | 0    |
| `numeric`     | `binary.BigEndian.PutUint64(num)` 8 字节      | `numeric.go`            | 0    |
| `reverse_bits`| `bits.Reverse64(num)` 后按大端写 8 字节       | `reverse_bits.go`       | 1    |
| `hash`        | 空密钥(null key)DES 加密大端 8 字节           | `hash.go`               | 1    |

`Cost()` 是官方给 vindex 打的"查询代价": `binary`/`numeric` 是 0(无需回表查映射),
`hash`/`reverse_bits` 是 1。源码里 `hash.go` 的注释还说明: 早期用 3DES, 但空密钥下
与 DES 完全等价, 故现用 DES。

本 demo 只实装前三种可精确复现的映射; `hash` 依赖 DES 分组密码, 未在本文件里重造,
仅以 `HASH_DOC` 记录其定义, 以免把"我猜的摘要"当成官方实现。
"""

from keyranges import KSID_LEN

HASH_DOC = (
    "hash vindex: vhash(shardKey uint64) = DES_encrypt(zeroKey, BigEndian(shardKey)), "
    "输出 8 字节 keyspace id; 因密钥为空, 3DES 与 DES 等价(见 hash.go 注释)。"
)


class Vindex(object):
    name = ""
    cost = 0
    unique = True
    reversible = True
    sequential = False

    def hash(self, value):
        raise NotImplementedError

    def unhash(self, ksid):
        raise NotImplementedError


class Binary(Vindex):
    """`binary`: 值本身就是 keyspace id(恒等映射)。"""

    name = "binary"
    cost = 0
    sequential = True

    def hash(self, value):
        return bytes(value)

    def unhash(self, ksid):
        return bytes(ksid)


class Numeric(Vindex):
    """`numeric`: uint64 的大端位模式, 直接当 keyspace id。

    注意这是**位模式映射**而不是散列: 连续的小整数会被塞进 key space 的同一端。
    """

    name = "numeric"
    cost = 0
    sequential = True

    def hash(self, value):
        return int(value).to_bytes(KSID_LEN, "big")

    def unhash(self, ksid):
        if len(ksid) != KSID_LEN:
            raise ValueError("numeric: keyspace id 长度必须为 8: %d" % len(ksid))
        return int.from_bytes(ksid, "big")


def reverse64(x):
    """`bits.Reverse64` 的等价实现: 反转 64 个比特。"""
    out = 0
    for i in range(64):
        if (x >> i) & 1:
            out |= 1 << (63 - i)
    return out


class ReverseBits(Vindex):
    """`reverse_bits`: 反转 uint64 的位序再大端写出。

    存在价值就是**打散** `numeric` 的聚集: 低位差异被搬到高位。
    """

    name = "reverse_bits"
    cost = 1
    sequential = False  # 源码 var 块里没有 Sequential, 故不实现 RangeMap

    def hash(self, value):
        return reverse64(int(value)).to_bytes(KSID_LEN, "big")

    def unhash(self, ksid):
        if len(ksid) != KSID_LEN:
            raise ValueError("reverse_bits: keyspace id 长度必须为 8: %d" % len(ksid))
        return reverse64(int.from_bytes(ksid, "big"))


REGISTRY = {}
for _cls in (Binary, Numeric, ReverseBits):
    REGISTRY[_cls.name] = _cls


def get(name):
    if name not in REGISTRY:
        raise KeyError("未实装的 vindex: %s(%s)" % (name, HASH_DOC if name == "hash" else ""))
    return REGISTRY[name]()
