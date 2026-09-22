"""ScyllaDB token -> shard 映射（对拍自 scylladb/scylladb dht/token.cc）。

源码口径（master 分支，实读）：

    inline unsigned
    zero_based_shard_of(uint64_t token, unsigned shards, unsigned sharding_ignore_msb_bits) {
        token <<= sharding_ignore_msb_bits;               // uint64 左移，高位丢弃
        return (uint128_t(token) * shards) >> 64;
    }

    unsigned
    shard_of(unsigned shard_count, unsigned msb, const token& t) {
        switch (t._kind) {
            case before_all_keys: return token::shard_of_minimum_token();   // 0
            case after_all_keys:  return shard_count - 1;
            case key:             return zero_based_shard_of(unbias(t), shard_count, msb);
        }
    }

    constexpr uint64_t unbias() const { return uint64_t(_data) + uint64_t(INT64_MIN); }
"""

MASK64 = (1 << 64) - 1
INT64_MIN = -(1 << 63)


class Token:
    """dht::token：三态（before_all_keys / key / after_all_keys），数据部分是 int64。"""

    BEFORE = "before_all_keys"
    KEY = "key"
    AFTER = "after_all_keys"

    def __init__(self, kind, data=0):
        self.kind = kind
        self.data = data

    @staticmethod
    def key(value):
        return Token(Token.KEY, value)

    @staticmethod
    def bias(n):
        """源码：token(kind::key, n - uint64(INT64_MIN)) —— unbias 的逆。"""
        v = (n - (1 << 63)) & MASK64  # 先按 uint64 取模
        if v >= (1 << 63):            # 再按 int64 解释（有符号回绕）
            v -= 1 << 64
        return Token(Token.KEY, v)

    @classmethod
    def minimum(cls):
        return cls(cls.BEFORE, 0)

    @classmethod
    def maximum(cls):
        return cls(cls.AFTER, 0)

    def is_minimum(self):
        return self.kind == Token.BEFORE

    def is_maximum(self):
        return self.kind == Token.AFTER

    def raw(self):
        # 源码：maximum 时 raw() 返回 int64 max，其他情况返回 _data
        return (1 << 63) - 1 if self.is_maximum() else self.data

    def unbias(self):
        # uint64(_data) + uint64(INT64_MIN)  —— 把 int64 环挪到 [0, 2^64)
        return ((self.data + INT64_MIN) & MASK64) if not self.is_maximum() else 0

    def __repr__(self):
        return "Token(%s,%d)" % (self.kind, self.data) if self.kind == Token.KEY else "Token(%s)" % self.kind


def zero_based_shard_of(token_u64, shards, msb):
    """主函数：把 token 当 [0,1) 的小数，shard = floor(0.token * shards)。"""
    shifted = (token_u64 << msb) & MASK64
    return ((shifted * shards) >> 64) & MASK64


def shard_of(shard_count, msb, t):
    """dht::shard_of —— 三态分派。"""
    if t.is_minimum():
        return 0  # token::shard_of_minimum_token()，源码注释 "hardcoded for now"
    if t.is_maximum():
        return shard_count - 1
    return zero_based_shard_of(t.unbias(), shard_count, msb)


def init_zero_based_shard_start(shards, msb):
    """zero_based_shard_of 的逆：ret[s] = 属于 s 的最小 token。"""
    if shards == 1:
        return [0]
    ret = []
    for s in range(shards):
        token = (s << 64) // shards
        token >>= msb
        # 除法向下取整可能落到前一个 shard，逐格 +1 修正到边界
        while zero_based_shard_of(token, shards, msb) != s:
            token += 1
        ret.append(token)
    return ret


def sharding_algorithm_name():
    return "biased-token-round-robin"


class StaticSharder:
    """dht::static_sharder（vnode 表）：对同一个配置，token -> shard 恒定不变。"""

    def __init__(self, shard_count, msb=0):
        self.shard_count = shard_count
        self.msb = msb
        self.shard_start = init_zero_based_shard_start(shard_count, msb)

    def shard_of(self, t):
        return shard_of(self.shard_count, self.msb, t)

    def shard_for_reads(self, t):
        return self.shard_of(t)

    def shard_for_writes(self, t, migrating=False):
        """tablet 迁移期间本地写要同时写给旧、新两个 shard（shard_replica_set 容量 2）。"""
        s = self.shard_of(t)
        if migrating:
            other = (s + 1) % self.shard_count
            return sorted({s, other}) if other != s else [s]
        return [s]

    def token_for_next_shard(self, t, shard, spans=1):
        """返回 > t 的、属于 shard 的第一个分片边界 token；溢出返回 maximum。"""
        if t.is_maximum() or shard >= self.shard_count:
            return Token.maximum()
        n = t.unbias() if not t.is_minimum() else 0
        s = zero_based_shard_of(n, self.shard_count, self.msb)
        # 源码语义：目标 shard 必须严格在当前 shard 之后（不回绕），否则视为溢出
        if self.msb == 0:
            n = self.shard_start[shard]
            if spans > 1 or shard <= s:
                return Token.maximum()
        else:
            left = (n >> (64 - self.msb)) + spans - (1 if shard > s else 0)
            if left >= (1 << self.msb):
                return Token.maximum()
            n = (left << (64 - self.msb)) | self.shard_start[shard]
        return Token.bias(n)

    def next_shard(self, t):
        shard = self.shard_for_reads(t)
        nxt = shard + 1 if shard + 1 != self.shard_count else 0
        tok = self.token_for_next_shard(t, nxt)
        if tok.is_maximum():
            return None
        return (nxt, tok)


class FixedShardPartitioner:
    """dht::fixed_shard_partitioner：token 编码 [shard:16][hash:48]，给 Raft 元数据表用。"""

    shard_bits = 16
    shard_shift = 64 - 16
    max_shard = 32767  # int16 max，源码写死
    hash_mask = (1 << shard_shift) - 1

    @classmethod
    def token_for_shard(cls, shard, hash_bits):
        value = (shard << cls.shard_shift) | (hash_bits & cls.hash_mask)
        return Token.key(value)

    @classmethod
    def shard_of(cls, t):
        return (t.raw() >> cls.shard_shift) & MASK64

    @classmethod
    def shard_of_clamped(cls, t, shard_count):
        """sharder 里额外 clamp：min(shard, shard_count - 1)。"""
        return min(cls.shard_of(t), shard_count - 1)
