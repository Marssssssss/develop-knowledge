"""652 控制流平坦化与反平坦化 —— 自检（实跑）。

A. T 表与 S 盒：与 CryptoUtils.cpp 里抄出的字面量逐项比对（独立证据）
B. scramble32：钉住取值 + 结构性质 + 负控
C. flatten 的决策过程与「原 CFG / 状态机」对拍
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import flatten as F  # noqa: E402

PASS = 0
FAIL = []


def check(c, m):
    global PASS
    if c:
        PASS += 1
    else:
        FAIL.append(m)


def eq(a, b, m):
    check(a == b, "%s: 期望 %r 实得 %r" % (m, b, a))


KEY = bytes(range(16))
ZERO = bytes(16)

# ------------------------------------------------ A. S 盒与 T 表

eq(F.SBOX[0], 0x63, "SBOX[0]")
eq(F.SBOX[1], 0x7c, "SBOX[1]")
eq(F.SBOX[0x53], 0xed, "SBOX[0x53]")
# CryptoUtils.cpp 里 AES_PRECOMP_TE0 的头四项
eq([hex(v) for v in F.TE0[:4]],
   ["0xc66363a5", "0xf87c7c84", "0xee777799", "0xf67b7b8d"], "TE0 前四项")
eq([hex(v) for v in F.TE1[:3]],
   ["0xa5c66363", "0x84f87c7c", "0x99ee7777"], "TE1 前三项")
eq([hex(v) for v in F.TE2[:3]],
   ["0x63a5c663", "0x7c84f87c", "0x7799ee77"], "TE2 前三项")
eq([hex(v) for v in F.TE3[:3]],
   ["0x6363a5c6", "0x7c7c84f8", "0x777799ee"], "TE3 前三项")
# 四张表互为字节右旋（TE1 = ROTR8(TE0) 等）
for i in (0, 1, 0x63, 0xff):
    eq(F.TE1[i], F.rotr32(F.TE0[i], 8), "TE1[%d] = ROTR8(TE0)" % i)
    eq(F.TE2[i], F.rotr32(F.TE0[i], 16), "TE2[%d] = ROTR16(TE0)" % i)
    eq(F.TE3[i], F.rotr32(F.TE0[i], 24), "TE3[%d] = ROTR24(TE0)" % i)
# xtime 与 GF 乘法
eq(F.xtime(0x63), 0xc6, "xtime(0x63)")
eq(F.xtime(0x80), 0x1b, "xtime(0x80) 模 0x11B")
eq(F.mul(0x57, 0x83), 0xc1, "GF 乘法 0x57*0x83")
eq(F.mul(0x53, 0xca), 1, "0x53 与 0xca 互逆")
eq(F.gf_inv(0x53), 0xca, "gf_inv(0x53)")


# ------------------------------------------------ B. scramble32

eq(F.scramble32(0, KEY), 0x4C2B67AF, "scramble32(0, KEY)")
eq(F.scramble32(1, KEY), 0x0827C599, "scramble32(1, KEY)")
eq(F.scramble32(2, KEY), 0x0C51DF22, "scramble32(2, KEY)")
eq(F.scramble32(0, ZERO), 0x76767676, "scramble32(0, 全零 key)")
eq(F.load32h(ZERO), 0, "LOAD32H(全零) = 0")
eq(F.load32h(bytes([0x12, 0x34, 0x56, 0x78] + [0] * 12)), 0x12345678, "LOAD32H 大端")

# 结构性质：全零 key 且输入为 0 时，四轮输入字节全同 ⇒ 结果四个字节相同
s0 = F.scramble32(0, ZERO)
eq(s0 & 0xFF, (s0 >> 8) & 0xFF, "全零 key 下结果的低两字节相同")
eq((s0 >> 16) & 0xFF, (s0 >> 24) & 0xFF, "全零 key 下结果的高两字节相同")

# 关键：最后那步 XOR 的是 key 前 4 字节，少了它就完全不同
def scramble_no_final_xor(value, key):
    k = [b & 0xFF for b in key]
    a = F.TE0[((value >> 24) ^ k[0]) & 0xFF] ^ F.TE1[((value >> 16) ^ k[1]) & 0xFF] \
        ^ F.TE2[((value >> 8) ^ k[2]) & 0xFF] ^ F.TE3[(value ^ k[3]) & 0xFF]
    b = F.TE0[((a >> 24) ^ k[4]) & 0xFF] ^ F.TE1[((a >> 16) ^ k[5]) & 0xFF] \
        ^ F.TE2[((a >> 8) ^ k[6]) & 0xFF] ^ F.TE3[(a ^ k[7]) & 0xFF]
    a = F.TE0[((b >> 24) ^ k[8]) & 0xFF] ^ F.TE1[((b >> 16) ^ k[9]) & 0xFF] \
        ^ F.TE2[((b >> 8) ^ k[10]) & 0xFF] ^ F.TE3[(b ^ k[11]) & 0xFF]
    b = F.TE0[((a >> 24) ^ k[12]) & 0xFF] ^ F.TE1[((a >> 16) ^ k[13]) & 0xFF] \
        ^ F.TE2[((a >> 8) ^ k[14]) & 0xFF] ^ F.TE3[(a ^ k[15]) & 0xFF]
    return b

check(scramble_no_final_xor(0, KEY) != F.scramble32(0, KEY), "少了末尾 XOR 结果必然不同")
eq(scramble_no_final_xor(0, ZERO), F.scramble32(0, ZERO), "全零 key 下末尾 XOR 是恒等的")

# 换 key 换值：这是 OLLVM 每次编译 case 值都不同的原因
check(F.scramble32(0, KEY) != F.scramble32(0, ZERO), "换 key 必换值")
check(F.scramble32(0, KEY) != F.scramble32(1, KEY), "相邻 case 值不同")
vals = {F.scramble32(i, KEY) for i in range(200)}
eq(len(vals), 200, "前 200 个 case 值互不冲突")


# ------------------------------------------------ C. flatten 决策过程

def diamond():
    b = {
        "entry": F.Block("entry", F.BR, ["B1", "B2"], cond="c0"),
        "B1": F.Block("B1", F.JMP, ["B3"]),
        "B2": F.Block("B2", F.JMP, ["B3"]),
        "B3": F.Block("B3", F.RET),
    }
    return F.CFG("entry", b)


cfg = diamond()
orig_true = F.run_original(cfg, {"c0": True})
orig_false = F.run_original(cfg, {"c0": False})
flat = F.flatten(cfg, KEY)
check(flat.ok, "diamond 应被平坦化")
eq(flat.reason, "", "成功时无 reason")
# 入口以条件分支结尾 ⇒ 被切出 entry.first，且它排在最前
eq(flat.order[0], "entry.first", "切出来的首块")
eq(len(flat.order), 4, "共 4 个块进 switch")
eq([hex(flat.case_of[n]) for n in flat.order],
   [hex(F.scramble32(i, KEY)) for i in range(4)], "case 值 = scramble32(下标)")
eq(flat.initial, flat.case_of["entry.first"], "switchVar 初值 = 首块 case")
eq(flat.transition["B3"], ("ret",), "ret 块不更新 switchVar")
eq(flat.transition["B1"], ("jmp", flat.case_of["B3"]), "单后继直接存常量")
eq(flat.transition["entry.first"][0], "select", "条件分支变成 select")

for cv, orig in ((True, orig_true), (False, orig_false)):
    seen, how = F.run_flattened(cfg, flat, {"c0": cv})
    eq(how, "ret", "状态机应正常返回(c0=%s)" % cv)
    # 原入口被切成 entry.first，后者承载了原来的条件分支
    want = ["entry.first" if b == "entry" else b for b in orig]
    eq(seen, want, "状态机访问序列 = 原 CFG(c0=%s)" % cv)

# 反平坦化还原出的边
edges = F.deflatten(flat)
eq(edges["entry.first"], ["B1", "B2"], "还原出 entry.first 的两条边")
eq(edges["B1"], ["B3"], "还原出 B1 -> B3")
eq(edges["B3"], [], "还原出 B3 无后继")


# 自环：不触发 fallback
def loop_cfg():
    b = {
        "le": F.Block("le", F.JMP, ["body"]),
        "body": F.Block("body", F.BR, ["body", "exit"], cond="c1"),
        "exit": F.Block("exit", F.RET),
    }
    return F.CFG("le", b)


lcfg = loop_cfg()
lflat = F.flatten(lcfg, KEY)
check(lflat.ok, "自环应被平坦化")
eq(len(lflat.order), 2, "入口不进 switch，只有 2 块")
eq(lflat.transition["body"][0], "select", "自环是 select")
eq(lflat.transition["body"][1], lflat.case_of["body"], "真分支回到自己")
seen, how = F.run_flattened(lcfg, lflat, {"c1": True}, limit=50)
eq(how, "loop", "自环会让状态机一直转")
eq(seen[:3], ["body", "body", "body"], "前三次都在 body")
seen, how = F.run_flattened(lcfg, lflat, {"c1": False})
eq(seen, ["body", "exit"], "退出分支")


# 回边指向入口 ⇒ findCaseDest 落空 ⇒ 兜底值
def backedge_cfg():
    b = {
        "e": F.Block("e", F.JMP, ["A"]),
        "A": F.Block("A", F.JMP, ["e"]),
        "B": F.Block("B", F.RET),
    }
    return F.CFG("e", b)


bcfg = backedge_cfg()
bflat = F.flatten(bcfg, KEY)
check(bflat.ok, "回边 CFG 应被平坦化")
eq(len(bflat.order), 2, "A/B 进 switch")
eq(bflat.fallback, F.scramble32(len(bflat.order) - 1, KEY), "兜底 = scramble32(块数-1)")
# 源码里 getNumCases() 此时已等于块数，所以「块数-1」正好是最后一块的 case
eq(bflat.fallback, bflat.case_of[bflat.order[-1]], "兜底值恰好等于最后一块的 case（源码口径）")
eq(bflat.transition["A"], ("jmp", bflat.fallback), "回边走兜底")
# 负控：若写成 getNumCases() 就会偏一格
check(F.scramble32(len(bflat.order), KEY) != bflat.fallback, "写成块数会偏一格")


# bail 路径
inv = F.CFG("e", {"e": F.Block("e", F.INVOKE, ["x"]), "x": F.Block("x", F.RET)})
r = F.flatten(inv, KEY)
check(not r.ok, "含 invoke 应放弃")
eq(r.reason, "invoke", "放弃原因是 invoke")

one = F.CFG("e", {"e": F.Block("e", F.RET)})
r = F.flatten(one, KEY)
check(not r.ok, "单块应放弃")
eq(r.reason, "single block", "放弃原因是单块")

# fixStack 把 phi 降级
pc = F.CFG("e", {
    "e": F.Block("e", F.JMP, ["P"]),
    "P": F.Block("P", F.BR, ["Q", "R"], cond="c", phi={"p": {"Q": "1", "R": "2"}}),
    "Q": F.Block("Q", F.JMP, ["R"]),
    "R": F.Block("R", F.RET),
})
pflat = F.flatten(pc, KEY)
eq(pflat.phis_demoted, ["p"], "phi p 被降级到栈")
eq(pc.blocks["P"].phi, {}, "降级后块里没有 phi 了")


print("断言通过: %d" % PASS)
if FAIL:
    print("失败 %d 条:" % len(FAIL))
    for m in FAIL:
        print("  -", m)
    sys.exit(1)
print("ALL GREEN")
