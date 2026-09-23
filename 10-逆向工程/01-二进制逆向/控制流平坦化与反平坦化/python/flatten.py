"""OLLVM 控制流平坦化（Flattening）与反平坦化。

原文实读：
  * obfuscator-llvm `lib/Transforms/Obfuscation/Flattening.cpp`（llvm-4.0 分支）
  * obfuscator-llvm `lib/Transforms/Obfuscation/Utils.cpp`（fixStack / toObfuscate）
  * obfuscator-llvm `lib/Transforms/Obfuscation/CryptoUtils.cpp`（scramble32、AES_PRECOMP_TE*）

本文件不引入 LLVM，只把 pass 的**决策过程**与 scramble32 的**位级公式**复刻出来，
再让「原 CFG 执行」与「平坦化后的状态机执行」逐条对拍。
"""

# ---------------------------------------------------------------- AES T 表

MASK32 = 0xFFFFFFFF


def xtime(a):
    """GF(2^8) 乘 2，模 0x11B。"""
    a <<= 1
    if a & 0x100:
        a ^= 0x11B
    return a & 0xFF


def gf_inv(a):
    """GF(2^8) 求逆（a=0 时返回 0）：扫一遍找 mul(a,x)==1。"""
    if a == 0:
        return 0
    for x in range(1, 256):
        if mul(a, x) == 1:
            return x
    return 0


def mul(a, b):
    """GF(2^8) 乘法，模 0x11B。"""
    r = 0
    for _ in range(8):
        if b & 1:
            r ^= a
        a = xtime(a)
        b >>= 1
    return r & 0xFF


def build_sbox():
    """AES S 盒：GF 逆 + 仿射变换。"""
    out = []
    for x in range(256):
        inv = gf_inv(x)
        s = inv
        for r in (1, 2, 3, 4):
            s ^= ((inv << r) | (inv >> (8 - r))) & 0xFF
        out.append(s ^ 0x63)
    return out


SBOX = build_sbox()


def rotr32(v, n):
    return ((v >> n) | (v << (32 - n))) & MASK32


def build_tables():
    """TE0 由 S 盒现算；TE1..TE3 是 TE0 的字节右旋（源码里四张表的关系）。"""
    te0 = []
    for x in range(256):
        s = SBOX[x]
        s2 = xtime(s)
        s3 = s2 ^ s
        te0.append((s2 << 24) | (s << 16) | (s << 8) | s3)
    te1 = [rotr32(v, 8) for v in te0]
    te2 = [rotr32(v, 16) for v in te0]
    te3 = [rotr32(v, 24) for v in te0]
    return te0, te1, te2, te3


TE0, TE1, TE2, TE3 = build_tables()


def load32h(key):
    """LOAD32H(tmpA, key)：取 key 前 4 字节的大端值。"""
    return ((key[0] & 0xFF) << 24) | ((key[1] & 0xFF) << 16) | \
           ((key[2] & 0xFF) << 8) | (key[3] & 0xFF)


def scramble32(value, key):
    """CryptoUtils::scramble32：四轮 T 表混合，最后与 key 前 4 字节异或。"""
    k = [b & 0xFF for b in key]
    a = TE0[((value >> 24) ^ k[0]) & 0xFF] \
        ^ TE1[((value >> 16) ^ k[1]) & 0xFF] \
        ^ TE2[((value >> 8) ^ k[2]) & 0xFF] \
        ^ TE3[(value ^ k[3]) & 0xFF]
    b = TE0[((a >> 24) ^ k[4]) & 0xFF] \
        ^ TE1[((a >> 16) ^ k[5]) & 0xFF] \
        ^ TE2[((a >> 8) ^ k[6]) & 0xFF] \
        ^ TE3[(a ^ k[7]) & 0xFF]
    a = TE0[((b >> 24) ^ k[8]) & 0xFF] \
        ^ TE1[((b >> 16) ^ k[9]) & 0xFF] \
        ^ TE2[((b >> 8) ^ k[10]) & 0xFF] \
        ^ TE3[(b ^ k[11]) & 0xFF]
    b = TE0[((a >> 24) ^ k[12]) & 0xFF] \
        ^ TE1[((a >> 16) ^ k[13]) & 0xFF] \
        ^ TE2[((a >> 8) ^ k[14]) & 0xFF] \
        ^ TE3[(a ^ k[15]) & 0xFF]
    return (load32h(k) ^ b) & MASK32


# ---------------------------------------------------------------- CFG 模型

RET, BR, JMP, INVOKE = "ret", "br", "jmp", "invoke"


class Block:
    def __init__(self, name, term, succ=(), cond=None, phi=None, ops=()):
        self.name = name
        self.term = term            # RET / BR / JMP / INVOKE
        self.succ = list(succ)      # 后继块名
        self.cond = cond            # BR 的条件名（求值器提供）
        self.phi = phi or {}        # {phi名: {来自块名: 值名}}
        self.ops = list(ops)        # 纯记号，供展示

    @property
    def nsucc(self):
        return 0 if self.term == RET else len(self.succ)


class CFG:
    def __init__(self, entry, blocks):
        self.entry = entry
        self.blocks = dict(blocks)

    def block(self, name):
        return self.blocks[name]


# ---------------------------------------------------------------- 平坦化

class Flattened:
    def __init__(self):
        self.ok = False
        self.reason = ""
        self.order = []        # 进 switch 的块名顺序
        self.case_of = {}      # 块名 -> case 值
        self.entry_name = ""   # 保留为 insert 的原始入口
        self.transition = {}   # 块名 -> ("ret",) / ("jmp", 值) / ("select", 真值, 假值)
        self.fallback = None   # findCaseDest 返回空时的兜底值
        self.initial = 0
        self.phis_demoted = []


def _split_entry(cfg):
    """入口以条件分支或多后继结尾时，pass 会 splitBasicBlock 切出一段。"""
    e = cfg.blocks[cfg.entry]
    if e.term == RET:
        return None
    if (e.term == BR) or e.nsucc > 1:
        newb = Block(e.name + ".first", e.term, list(e.succ), e.cond)
        e.term = JMP
        e.succ = [newb.name]
        cfg.blocks[newb.name] = newb
        return newb.name
    return None


def flatten(cfg, key):
    """复刻 Flattening::flatten 的决策过程（不含 IR 构造）。"""
    f = Flattened()
    names = list(cfg.blocks.keys())
    for n in names:
        if cfg.blocks[n].term == INVOKE:
            f.reason = "invoke"
            return f
    if len(names) <= 1:
        f.reason = "single block"
        return f

    order = [n for n in names if n != cfg.entry]
    first = _split_entry(cfg)
    if first is not None:
        order.insert(0, first)
    if not order:
        f.reason = "nothing to flatten"
        return f

    # case 值：逐个 addCase，取值是当时的 getNumCases()
    for i, n in enumerate(order):
        f.case_of[n] = scramble32(i, key)
    f.order = order
    f.entry_name = cfg.entry
    f.initial = scramble32(0, key)
    f.fallback = scramble32(len(order) - 1, key)

    for n in order:
        b = cfg.blocks[n]
        if b.nsucc == 0:
            f.transition[n] = ("ret",)
        elif b.nsucc == 1:
            s = b.succ[0]
            v = f.case_of.get(s)
            if v is None:
                v = f.fallback
            f.transition[n] = ("jmp", v)
        else:
            t = f.case_of.get(b.succ[0])
            fl = f.case_of.get(b.succ[1])
            if t is None:
                t = f.fallback
            if fl is None:
                fl = f.fallback
            f.transition[n] = ("select", t, fl)
    f.ok = True

    # fixStack：所有 phi 与逃逸寄存器被降级到栈
    for n in list(cfg.blocks.keys()):
        b = cfg.blocks[n]
        if b.phi:
            f.phis_demoted.extend(sorted(b.phi.keys()))
            b.phi = {}
    return f


# ---------------------------------------------------------------- 执行对拍

def run_original(cfg, cond_values, limit=1000):
    """按原 CFG 执行，返回访问过的块名序列。"""
    cur = cfg.entry
    seen = []
    for _ in range(limit):
        b = cfg.blocks[cur]
        seen.append(cur)
        if b.term == RET or b.nsucc == 0:
            return seen
        if b.term == BR:
            cur = b.succ[0] if cond_values.get(b.cond) else b.succ[1]
        else:
            cur = b.succ[0]
    return seen


def run_flattened(cfg, flat, cond_values, limit=1000):
    """按平坦化后的状态机执行，返回访问过的块名序列。"""
    state = flat.initial
    seen = []
    dispatch = {v: n for n, v in flat.case_of.items()}
    for _ in range(limit):
        n = dispatch.get(state)
        if n is None:
            return seen, "dead"          # 落到 switchDefault
        b = cfg.blocks[n]
        seen.append(n)
        t = flat.transition[n]
        if t[0] == "ret":
            return seen, "ret"
        if t[0] == "jmp":
            state = t[1]
        else:
            state = t[1] if cond_values.get(b.cond) else t[2]
    return seen, "loop"


def deflatten(flat):
    """反平坦化：从 case 值反推出真实的块间转移（逆查表）。"""
    val2name = {v: n for n, v in flat.case_of.items()}
    edges = {}
    for n, t in flat.transition.items():
        if t[0] == "ret":
            edges[n] = []
        elif t[0] == "jmp":
            edges[n] = [val2name.get(t[1], "<default>")]
        else:
            edges[n] = [val2name.get(t[1], "<default>"), val2name.get(t[2], "<default>")]
    return edges
