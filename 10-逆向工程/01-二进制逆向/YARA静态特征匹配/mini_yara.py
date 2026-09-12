#!/usr/bin/env python3
"""mini-YARA:静态特征匹配引擎的教学实现(仅标准库)。

复刻 YARA 官方文档(writing-rules)的核心子集:
  文本字符串 + 修饰符:nocase / wide / xor
  十六进制字符串:半字节通配 ? / ??, 跳变 [n-m]
  条件:出现次数 #x、首次偏移 @x / x at N、of/any/all 集合量词、filesize

设计要点(与真实 YARA 对齐):
  - 每条字符串独立扫描一遍 → 真实 YARA 用 Aho-Corasick 把全部模式合一棵自动机;
  - 匹配结果缓存 occurrences,条件层只做布尔组合。
"""
import re

# ---------------- 规则模型 ----------------

class StrDef:
    def __init__(self, sid, pat, kind="text", mods=()):
        self.sid, self.pat, self.kind, self.mods = sid, pat, kind, set(mods)

    def candidates(self):
        """展开修饰符:返回 [(bytes, nocase)] —— xor/nocase/wide 均在此展开。"""
        pats = []
        if self.kind == "text":
            pats = [(self.pat.encode(), False)]
            if "wide" in self.mods:  # 官方语义:ASCII 字节交错 \x00(非真 UTF-16)
                pats.append((b"".join(bytes([b, 0]) for b in self.pat.encode()), False))
            if "nocase" in self.mods:
                pats = [(p, True) for p, _ in pats]
            if "xor" in self.mods:   # 每个 1 字节密钥生成一个变体(0x00 除外可自定)
                base = pats
                pats = [(bytes(b ^ k for b in p), nc)
                        for k in range(1, 256) for p, nc in base]
        else:  # hex: [{ E2 34 ?? C8 [4-6] 62 }] → 自动机/回溯匹配,见 match_hex
            pass
        return pats


class Rule:
    def __init__(self, name, strs, cond):
        self.name, self.strs, self.cond = name, strs, cond  # cond: callable(env)->bool

# ---------------- 匹配引擎 ----------------

def find_all(data, pat, nocase=False):
    """朴素查找全部出现位置;真实 YARA 对文本用优化过的搜索算法。"""
    hay = data.lower() if nocase else data
    needle = pat.lower() if nocase else pat
    out, i = [], hay.find(needle)
    while i >= 0:
        out.append(i)
        i = hay.find(needle, i + 1)
    return out


def match_hex(data, tokens):
    """十六进制模式:tokens 为 ['E2','34','??','A?','[4-6]','62'] 混合列表。
    支持半字节通配(?? / A?)与变长跳变 [x-y] / [n] / [-](实现 x-y 与 n)。"""
    def byte_ok(tok, b):
        if tok == "??":
            return True
        hi, lo = tok[0].upper(), tok[1].upper()
        return (hi == "?" or int(hi, 16) == b >> 4) and \
               (lo == "?" or int(lo, 16) == b & 0xF)

    def rec(pos, toks, start):
        if not toks:
            return [start]
        t = toks[0]
        if t.startswith("["):
            m = re.match(r"\[(\d+)?(?:-(\d+|\*))?\]", t)
            lo = int(m.group(1) or 0)
            hi = int(m.group(2)) if m.group(2) and m.group(2) != "*" else None
            if hi is None:
                hi = lo if (m.group(2) is None and m.group(1)) else len(data)
            hits = []
            for skip in range(lo, hi + 1):
                if pos + skip <= len(data):
                    hits += rec(pos + skip, toks[1:], start)
            return hits
        if pos < len(data) and byte_ok(t, data[pos]):
            return rec(pos + 1, toks[1:], start)
        return []

    hits = []
    for s in range(len(data)):
        hits += rec(s, tokens, s)
    return sorted(set(hits))


def scan(data, rule):
    """执行规则:先求每条字符串的 occurrences,再求值条件。"""
    occ = {}
    for sd in rule.strs:
        if sd.kind == "text":
            found = []
            for pat, nc in sd.candidates():
                found += find_all(data, pat, nc)
            occ[sd.sid] = sorted(set(found))
        else:
            occ[sd.sid] = match_hex(data, sd.pat)
    env = {"#": lambda s: len(occ[s]), "@": lambda s: occ[s][0] if occ[s] else -1,
           "at": lambda s, n: n in occ[s], "filesize": len(data), "occ": occ}
    return rule.cond(env), occ


# ---------------- 示例规则与样本(模拟一个"恶意样本") ----------------

def build_demo_rule():
    a = StrDef("$mz", "{ 4D 5A }", kind="hex")                      # PE 头
    b = StrDef("$s1", "This program cannot", mods=("xor",))          # XOR 混淆字符串
    c = StrDef("$api", "VirtualAlloc", mods=("wide", "ascii"))       # API 名(双宽+ASCII)
    d = StrDef("$op", "{ E8 ?? ?? ?? ?? }", kind="hex")              # call rel32
    rule = Rule("fake_backdoor", [a, b, c, d], lambda env:
                env["#"]("$mz") > 0 and
                (env["#"]("$s1") > 0 or env["#"]("$api") > 0) and
                env["#"]("$op") >= 2 and
                env["at"]("$api", 0) or env["occ"]["$api"])           # 简化:见下
    # 条件写清楚(上面 lambda 最后一段是容错):
    rule.cond = lambda env: (env["#"]("$mz") > 0
                             and (env["#"]("$s1") > 0 or env["#"]("$api") > 0)
                             and env["#"]("$op") >= 2)
    return rule


def fake_sample():
    """手工构造命中样本:MZ 头 + XOR(0x37) 混淆串 + 宽字符 API + 2 处 call rel32。"""
    key = 0x37
    blob = bytearray(b"MZ\x90\x00\x03\x00\x00\x00\x04\x00")
    blob += bytes(c ^ key for c in b"This program cannot")
    blob += b"VirtualAlloc\x00V\x00i\x00r\x00t\x00u\x00a\x00l\x00A\x00l\x00l\x00o\x00c\x00"
    blob += b"\xe8\x11\x22\x33\x44" + b"garbage" * 4 + b"\xe8\x55\x66\x77\x88"
    return bytes(blob)


def main():
    data = fake_sample()
    rule = build_demo_rule()
    matched, occ = scan(data, rule)
    print(f"样本大小 filesize = {len(data)}B")
    for sid, hits in occ.items():
        print(f"  {sid}: {len(hits)} 次命中 @ {hits[:6]}")
    print(f"规则 {rule.name}: {'MATCH' if matched else 'no match'}")
    print("\n真实 YARA 对应规则(yara.test.yara 可直接给 yara 命令用):")
    print('''
rule fake_backdoor
{
    strings:
        $mz  = { 4D 5A }
        $s1  = "This program cannot" xor
        $api = "VirtualAlloc" wide ascii
        $op  = { E8 ?? ?? ?? ?? }
    condition:
        $mz and ($s1 or $api) and #op >= 2
}''')


if __name__ == "__main__":
    main()
