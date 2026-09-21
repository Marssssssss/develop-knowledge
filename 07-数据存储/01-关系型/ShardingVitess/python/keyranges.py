"""Vitess key range 与分区语义。

逐行转写自官方文档《Sharding》(vitess.io/docs/reference/features/sharding/):

- 键范围(key range) 是连续的 keyspace ID 区间, **含起点、不含终点**;
  空起点代表最小值(所有值都大于它), 空终点代表大于最大可能值。
- 分片名 = 起止值的十六进制, 用连字符分隔, 如 `80-c0`。
- Vitess 计算分片时把分片键**左对齐**成二进制串, 因此右侧的 0 是**无意义且可省略**的;
  于是 `0x80` 是正中值: 两分片时 < 0x80 的落第一个, >= 0x80 的落第二个。
- 若干分片要构成完整分区(partition): 首尾必须各有一个空端, 且彼此首尾相接。
"""

KSID_LEN = 8


def canonical(ksid):
    """左对齐规范化: 右侧补 0 到 8 字节。

    文档原话: "Vitess always converts sharding keys to a left-justified binary
    string ... the right-most zeroes are insignificant and optional"。
    因此 b'\\x80'、b'\\x80\\x00'、b'\\x80' + b'\\x00' * 6 是同一个值。
    """
    if len(ksid) > KSID_LEN:
        raise ValueError("keyspace id too long: %r" % (ksid,))
    return ksid + b"\x00" * (KSID_LEN - len(ksid))


def hexs(b):
    """分片名里用的十六进制: 空串保持为空(代表无端点)。"""
    return b.hex() if b else ""


class KeyRange(object):
    """[start, end) —— 含起点、不含终点。"""

    def __init__(self, start=b"", end=b""):
        self.start = start
        self.end = end

    def contains(self, ksid):
        k = canonical(ksid)
        if self.start and k < canonical(self.start):
            return False
        if self.end and k >= canonical(self.end):
            return False
        return True

    def name(self):
        return "%s-%s" % (hexs(self.start), hexs(self.end))

    def __repr__(self):
        return "KeyRange(%s)" % self.name()


def parse_shard_name(name):
    """'80-c0' -> KeyRange; '-80' / 'c0-' / '-' 分别为左开放/右开放/全区间。"""
    if name.count("-") != 1:
        raise ValueError("bad shard name: %r" % (name,))
    left, right = name.split("-")
    start = bytes.fromhex(left) if left else b""
    end = bytes.fromhex(right) if right else b""
    return KeyRange(start, end)


def validate_partition(names):
    """判断一组分片名是否构成完整分区(覆盖全空间、首尾相接、不重叠)。"""
    ranges = [parse_shard_name(n) for n in names]
    ranges.sort(key=lambda r: canonical(r.start))
    if ranges[0].start:
        return False, "最左分片的起点必须为空(代表最小值)"
    if ranges[-1].end:
        return False, "最右分片的终点必须为空(代表最大值)"
    for a, b in zip(ranges, ranges[1:]):
        if canonical(a.end) != canonical(b.start):
            return False, "分片 %s 与 %s 不相接" % (a.name(), b.name())
    return True, "完整分区(%d 个分片)" % len(ranges)


def generate_shard_ranges(n):
    """按 n 等分生成分片名, 与文档 `GenerateShardRanges` 的十六进制切分一致。

    只支持 2 的幂: 文档示例(-40 / 40-80 / 80-c0 / c0-)全按二进制位切;
    非幂次等分在 2^64 空间上不整除, 官方未规定其行为, 此处直接报错而不是编造。
    """
    if n & (n - 1) != 0:
        raise ValueError("只支持 2 的幂分片数: %d" % n)
    step = (1 << 64) // n
    bounds = [b""] + [i * step for i in range(1, n)] + [1 << 64]
    names = []
    for i in range(n):
        start = _left_justified(bounds[i]) if i > 0 else b""
        end = _left_justified(bounds[i + 1]) if i < n - 1 else b""
        names.append("%s-%s" % (hexs(start), hexs(end)))
    return names


def _left_justified(value):
    """把 8 字节整数值写成去掉右侧 0 的最短十六进制字节串。"""
    raw = value.to_bytes(KSID_LEN, "big")
    end = len(raw)
    while end > 1 and raw[end - 1] == 0:
        end -= 1
    return raw[:end]


def split_shard(name):
    """把分片对半切成两个子分片, 返回子分片名(resharding 的切分动作)。"""
    kr = parse_shard_name(name)
    lo = int.from_bytes(canonical(kr.start), "big")
    hi = int.from_bytes(canonical(kr.end), "big") if kr.end else (1 << 64)
    mid = (lo + hi) // 2
    left = "%s-%s" % (hexs(kr.start), hexs(_left_justified(mid)))
    right_end = hexs(kr.end)
    right = "%s-%s" % (hexs(_left_justified(mid)), right_end)
    return [left, right]


def locate(shards, ksid):
    """返回包含该 keyspace id 的分片名; 分区完整时必唯一。"""
    hits = [n for n in shards if parse_shard_name(n).contains(ksid)]
    return hits
