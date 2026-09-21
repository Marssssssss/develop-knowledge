"""CRC 参数反解：只给若干 (报文, 校验和) 样本，反推出 width/poly/init/refin/refout/xorout。

思路（对应 `reveng -s` 那类工具的核心）：

1. **width** 由校验字段的字节数给出（本 demo 当作已知输入）；
2. **poly**：用「等长报文的 CRC 之差」筛。因为
   `crc(m; init) = crc(m; 0) ^ prop(init, |m|) ^ xorout`，
   其中 `prop` 只依赖 poly 与**长度**，所以对**等长**的两条报文取异或，
   init 与 xorout 的贡献会**整体抵消**——于是可以只搜 poly（2^W 个候选）；
3. **init / xorout**：poly 定下来之后，xorout 由任意一条样本直接反解，
   init 在 2^W 里暴力搜（W ≤ 16 可行）；
4. 最后用**函数等价**判定成败——参数组本来就不唯一（见 README）。

运行：python main.py
"""

from __future__ import annotations

import random

from crc_model import (CATALOGUE, Model, catalogue_models, crc, crc_fast,
                       functional_key, reflect)


def samples(model, n=10, seed=1, minlen=1, maxlen=24):
    """造 (报文, CRC) 样本。"""
    rnd = random.Random(seed)
    out = []
    for _ in range(n):
        ln = rnd.randrange(minlen, maxlen + 1)
        msg = bytes(rnd.randrange(256) for _ in range(ln))
        out.append((msg, crc_fast(model, msg)))
    return out


def crc0_poly(width, poly, data):
    """init=0 / xorout=0 下的寄存器残值（不建表，短报文时比查表快得多）。

    这是 poly 搜索的内层循环：2^W 个候选各算两次，建 256 项表会让它慢 100 倍。
    """
    mask = (1 << width) - 1
    top = 1 << (width - 1)
    reg = 0
    for byte in data:
        reg ^= byte << (width - 8)
        for _ in range(8):
            reg = ((reg << 1) ^ poly) & mask if (reg & top) else (reg << 1) & mask
    return reg


def search_polys(pairs, width, refin, refout):
    """用等长报文对的「残值之差」不变式筛 poly。

    不变式：对**等长**的两条报文，init 的传播项与 xorout 都会抵消，于是
    两条报文残值之差只由 poly 决定（refout 是线性置换，搬到等式右边即可）。
    探针取**最短**的一对等长报文——报文不能截断，截断后 CRC 与原报文无关。
    """
    lens = {}
    for i, (m, _c) in enumerate(pairs):
        lens.setdefault(len(m), []).append(i)
    groups = sorted((ix for ix in lens.values() if len(ix) >= 2),
                    key=lambda ix: len(pairs[ix[0]][0]))
    if not groups:
        return []
    a, b = groups[0][0], groups[0][1]
    ma, ca = pairs[a]
    mb, cb = pairs[b]
    if refin:
        ma = bytes(reflect(x, 8) for x in ma)
        mb = bytes(reflect(x, 8) for x in mb)
    target = ca ^ cb
    if refout:
        target = reflect(target, width)
    cand = []
    for poly in range(1 << width):
        if crc0_poly(width, poly, ma) ^ crc0_poly(width, poly, mb) == target:
            cand.append(poly)
    return cand


def solve_init_xorout(poly, width, refin, refout, pairs):
    """poly 已定，暴力搜 init（xorout 由首条样本反解），返回全部可行解。"""
    out = []
    m0, c0 = pairs[0]
    probe = Model(width, poly, 0, refin, refout, 0)
    for init in range(1 << width):
        probe.init = init
        xorout = crc_fast(probe, m0) ^ c0
        # 先用第二条样本剪枝：xorout 由首条反解而来，首条本身不构成约束
        if len(pairs) > 1:
            g, c = pairs[1]
            if crc_fast(probe, g) ^ xorout != c:
                continue
        cand = Model(width, poly, init, refin, refout, xorout)
        if all(crc_fast(cand, g) == c for g, c in pairs):
            out.append(cand)
    return out


def recover(pairs, width, max_init_bits=16):
    """完整反解。返回所有能复现样本的 Model（参数组可能不唯一）。"""
    found = []
    for refin in (False, True):
        for refout in (False, True):
            for poly in search_polys(pairs, width, refin, refout):
                if width > max_init_bits:
                    continue
                found.extend(solve_init_xorout(poly, width, refin, refout, pairs))
    return found


def equivalent(a, b, seed=99):
    """函数等价：在随机报文上输出一致。"""
    rnd = random.Random(seed)
    for _ in range(64):
        msg = bytes(rnd.randrange(256) for _ in range(rnd.randrange(0, 48)))
        if crc_fast(a, msg) != crc_fast(b, msg):
            return False
    return True


def demo():
    by_name = {m.name: m for m in catalogue_models()}
    for name in ("CRC-8/SMBUS", "CRC-16/MODBUS", "CRC-16/CCITT-FALSE"):
        if name not in by_name:
            continue
        m = by_name[name]
        pairs = samples(m, n=10, seed=5)
        found = recover(pairs, m.width)
        good = [f for f in found if equivalent(f, m)]
        print("%-22s width=%d  候选解 %d 个，其中函数等价 %d 个" %
              (name, m.width, len(found), len(good)))
        for g in good[:3]:
            print("    poly=0x%x init=0x%x refin=%s refout=%s xorout=0x%x"
                  % (g.poly, g.init, g.refin, g.refout, g.xorout))
        print("    真值:  poly=0x%x init=0x%x refin=%s refout=%s xorout=0x%x"
              % (m.poly, m.init, m.refin, m.refout, m.xorout))


if __name__ == "__main__":
    demo()
