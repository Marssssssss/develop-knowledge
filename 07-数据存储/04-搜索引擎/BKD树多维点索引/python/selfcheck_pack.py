"""697 BKD 树 —— 自检（索引前缀编码 / split 值单调性）。

运行：python selfcheck_pack.py
"""

import random
import sys

from bkd import BKDConfig
from bkdtree import build_tree, pack_index, _to_bytes_list, _from_bytes
from checkutil import eq, ok, report


def decode_packed(tree, codes, cfg):
    """按 BKDReader.readNodeData 的语义把 code 流还原成 splitValue 序列。"""
    bpd = cfg.bytes_per_dim
    out = []
    pos = [0]

    def walk(node, last, neg):
        if node.kind != "inner":
            return
        c = codes[pos[0]]
        pos[0] += 1
        dim = c["code"] % cfg.num_index_dims
        rest = c["code"] // cfg.num_index_dims
        prefix = rest % (1 + bpd)
        delta = rest // (1 + bpd)
        ok("code 解出 splitDim", dim == node.split_dim, (c, node.split_dim))
        ok("code 解出 prefix", prefix == c["prefix"])
        ok("code 解出 delta", delta == c["delta"])
        ok("delta 恒正（源码 assert）", delta >= 0, c)
        if prefix < bpd:
            d = -delta if neg[dim] else delta
            bs = _to_bytes_list(last[dim], bpd)
            bs[prefix] = (bs[prefix] + d) & 0xFF
            for j, b in enumerate(c["suffix"]):
                bs[prefix + 1 + j] = b
            v = _from_bytes(bs)
        else:
            v = last[dim]
        out.append((dim, v))
        nl = dict(last)
        nl[dim] = v
        a = dict(neg)
        a[dim] = True
        walk(node.left, nl, a)
        b = dict(neg)
        b[dim] = False
        walk(node.right, nl, b)

    walk(tree, {d: 0 for d in range(cfg.num_index_dims)},
         {d: False for d in range(cfg.num_index_dims)})
    eq("code 数 == 内节点数", pos[0], len(codes))
    return out


def t_pack():
    rnd = random.Random(31337)
    for trial in range(30):
        nd = rnd.choice([1, 2, 3])
        ni = min(nd, rnd.choice([1, 2, 3]))
        bpd = rnd.choice([1, 2, 4])
        cfg = BKDConfig(nd, ni, bpd, rnd.choice([2, 3, 4, 16]))
        npts = rnd.randrange(2, 90)
        m = 1 << (8 * bpd)
        pts = [tuple(rnd.randrange(m) for _ in range(nd)) for _ in range(npts)]
        tree = build_tree(list(zip(range(npts), pts)), cfg)
        codes = pack_index(tree, cfg)

        truth = []

        def walk(n):
            if n.kind == "inner":
                truth.append((n.split_dim, n.split_value))
                walk(n.left)
                walk(n.right)
        walk(tree)
        dec = decode_packed(tree, codes, cfg)
        eq("解码出的维度序列", [t[0] for t in dec], [t[0] for t in truth])
        for i, (got, want) in enumerate(zip(dec, truth)):
            eq("解码出的 splitValue[%d]" % i, got[1], want[1])
        for c in codes:
            eq("suffix 长度 == bytesPerDim-prefix-1",
               len(c["suffix"]), max(0, bpd - c["prefix"] - 1))

    # 前缀编码确实省字节：朴素要 内节点数 * bytesPerDim
    rnd2 = random.Random(11)
    cfg = BKDConfig(2, 2, 8, 32)
    npts = 600
    pts = [(rnd2.randrange(1 << 32), rnd2.randrange(1 << 32)) for _ in range(npts)]
    tree = build_tree(list(zip(range(npts), pts)), cfg)
    codes = pack_index(tree, cfg)
    naive = len(codes) * cfg.bytes_per_dim
    packed = sum(1 + len(c["suffix"]) for c in codes)   # 1 = 靠 delta 还原的那字节
    ok("前缀编码比朴素省字节", packed < naive, (packed, naive))
    decode_packed(tree, codes, cfg)


def t_monotone():
    """negativeDeltas 之所以能「左 true / 右 false」，靠的就是路径上 split 值单调。

    这里直接验证这个前提：沿任一路径，同一维度上的 split 值单调不减/不增。
    """
    rnd = random.Random(4242)
    for _ in range(20):
        cfg = BKDConfig(3, 3, 2, 4)
        npts = rnd.randrange(4, 60)
        m = 1 << (8 * cfg.bytes_per_dim)
        pts = [tuple(rnd.randrange(m) for _ in range(3)) for _ in range(npts)]
        tree = build_tree(list(zip(range(npts), pts)), cfg)

        def walk(node, seen):
            if node.kind == "leaf":
                return
            d = node.split_dim
            if d in seen:
                prev, direction = seen[d]
                if direction == "left":
                    ok("左子树内 split 值不增", node.split_value <= prev,
                       (prev, node.split_value, d))
                else:
                    ok("右子树内 split 值不减", node.split_value >= prev,
                       (prev, node.split_value, d))
            a = dict(seen)
            a[d] = (node.split_value, "left")
            walk(node.left, a)
            b = dict(seen)
            b[d] = (node.split_value, "right")
            walk(node.right, b)
        walk(tree, {})


def main():
    t_pack()
    t_monotone()
    report("selfcheck_pack")


if __name__ == "__main__":
    sys.exit(main())
