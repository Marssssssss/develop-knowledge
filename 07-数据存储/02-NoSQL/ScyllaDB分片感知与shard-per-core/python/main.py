"""ScyllaDB shard-per-core 与分片感知驱动的代价模型。

模型口径（README 有说明）：
- token 源用 blake2b 前 8 字节当 int64，代替官方 murmur3_x64_128 的低 64 位；
  只为拿到一个确定性、均匀分布的 int64，不影响 shard 映射的验证。
- 节点选择用「token 落在哪个 vnode 区间」，shard 选择用官方 `zero_based_shard_of`。
- 分片感知驱动：coordinator 直接把请求发到持有该 token 的 CPU（shard）。
  非分片感知：请求落到节点上的**任意** shard，再由该 shard 转发给真正的 owner。
"""

import hashlib
import struct

from sharding import StaticSharder, Token

MURMUR3_SUBSTITUTE = "blake2b-8bytes"


def token_for_key(key):
    """确定性 int64 token。官方是 murmur3_x64_128 的 hash[0]，这里用 blake2b 替代。"""
    digest = hashlib.blake2b(key.encode("utf-8"), digest_size=8).digest()
    (value,) = struct.unpack("<q", digest)
    return value


class Cluster:
    """nodes 个节点，每节点 shards 个 shard；vnode 用均匀切分的 token 环近似。"""

    def __init__(self, nodes, shards, vnodes=8, msb=0):
        self.nodes = nodes
        self.shards = shards
        self.vnodes = vnodes
        self.sharder = StaticSharder(shards, msb)
        # 把 int64 环均分给 nodes*vnodes 个 vnode
        self.slots = nodes * vnodes
        self.span = (1 << 64) // self.slots

    def node_of(self, token_value):
        """token -> 节点：按 unbias 后的位置落在哪个 vnode 槽。"""
        t = Token.key(token_value)
        pos = t.unbias()
        slot = pos // self.span if self.span else 0
        return (slot % self.slots) % self.nodes

    def shard_of(self, token_value):
        return self.sharder.shard_of(Token.key(token_value))


def route(cluster, keys, shard_aware):
    """返回 (直连命中数, 需要节点内跨核转发的请求数)。"""
    direct = 0
    forwarded = 0
    for i, k in enumerate(keys):
        tv = token_for_key(k)
        owner_shard = cluster.shard_of(tv)
        # 非分片感知驱动：连接是随机建立的，落到节点上的哪个 shard 由连接决定
        landed = owner_shard if shard_aware else (i % cluster.shards)
        if landed == owner_shard:
            direct += 1
        else:
            forwarded += 1
    return direct, forwarded


def demo():
    cluster = Cluster(nodes=3, shards=8)
    keys = ["user:%d" % i for i in range(1000)]

    print("ScyllaDB shard-per-core 路由代价（3 节点 × 8 shard，1000 个 key）")
    print("token 源: %s（替代 murmur3，仅影响分布不影响映射）" % MURMUR3_SUBSTITUTE)
    aware_d, aware_f = route(cluster, keys, True)
    dumb_d, dumb_f = route(cluster, keys, False)
    print("  分片感知   : 直连 %4d / 跨核转发 %4d" % (aware_d, aware_f))
    print("  非分片感知 : 直连 %4d / 跨核转发 %4d" % (dumb_d, dumb_f))
    print("  非分片感知的期望直连率 = 1/shards = %.4f" % (1.0 / cluster.shards))
    print("  实测直连率 = %.4f" % (dumb_d / float(len(keys))))

    print()
    print("token -> shard 映射（前 6 个 key，8 shard，msb=0）")
    for k in keys[:6]:
        tv = token_for_key(k)
        print("  %-8s token=%-22d node=%d shard=%d"
              % (k, tv, cluster.node_of(tv), cluster.shard_of(tv)))

    print()
    print("sharding_ignore_msb_bits 的影响（同一个 token，不同 msb）")
    tv = token_for_key("user:0")
    for msb in (0, 1, 2, 4):
        s = StaticSharder(8, msb)
        print("  msb=%d -> shard %d" % (msb, s.shard_of(Token.key(tv))))

    print()
    print("tablet 迁移期：写入要同时落两个 shard")
    s = StaticSharder(8)
    t = Token.key(token_for_key("user:0"))
    print("  平时 shard_for_writes  = %s" % s.shard_for_writes(t))
    print("  迁移中 shard_for_writes = %s" % s.shard_for_writes(t, migrating=True))


if __name__ == "__main__":
    demo()
