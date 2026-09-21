"""Go unicode/utf8 模型的自检 + 与 Python codec 的对拍。"""

import sys

from utf8model import (
    RUNE_ERROR, RUNE_SELF, MAX_RUNE, UTF_MAX,
    SURROGATE_MIN, SURROGATE_MAX,
    MASKX, MASK2, MASK3, MASK4,
    T_X, T_2, T_3, T_4,
    RUNE1_MAX, RUNE2_MAX, RUNE3_MAX, LOCB, HICB,
    XX, AS, S1, S2, S3, S4, S5, S6, S7,
    FIRST, ACCEPT_RANGES,
    decode_rune, rune_len, encode_rune, rune_count_in_string, rune_start,
    rune_error_bytes, valid,
)

PASS = [0]
FAIL = [0]


def ok(cond, label):
    if cond:
        PASS[0] += 1
    else:
        FAIL[0] += 1
        print("FAIL: " + label)


def eq(got, want, label):
    ok(got == want, "%s (got=%r want=%r)" % (label, got, want))


def tu(r_or_pair):
    if isinstance(r_or_pair, tuple):
        return r_or_pair
    return (r_or_pair.r, r_or_pair.size)


# ---------------------------------------------------------------- E1 常量
def e1_constants():
    eq(RUNE_ERROR, 0xFFFD, "E1 RuneError=U+FFFD")
    eq(RUNE_SELF, 0x80, "E1 RuneSelf=0x80")
    eq(MAX_RUNE, 0x10FFFF, "E1 MaxRune=U+10FFFF")
    eq(UTF_MAX, 4, "E1 UTFMax=4")
    eq((SURROGATE_MIN, SURROGATE_MAX), (0xD800, 0xDFFF), "E1 代理区范围")
    eq((MASKX, MASK2, MASK3, MASK4), (0x3F, 0x1F, 0x0F, 0x07), "E1 四个 mask")
    eq((T_X, T_2, T_3, T_4), (0x80, 0xC0, 0xE0, 0xF0), "E1 四个前缀 tag")
    eq((RUNE1_MAX, RUNE2_MAX, RUNE3_MAX), (127, 2047, 65535), "E1 三个长度上界")
    eq((LOCB, HICB), (0x80, 0xBF), "E1 默认续字节区间")
    eq(rune_error_bytes(), [0xEF, 0xBF, 0xBD], "E1 RuneError 的三字节编码")


# ---------------------------------------------------------------- E2 first 表
def e2_first_table():
    eq(len(FIRST), 256, "E2 first 表 256 项")
    ok(all(v == AS for v in FIRST[0x00:0x80]), "E2 0x00-0x7F 全是 ASCII")
    ok(all(v == XX for v in FIRST[0x80:0xC0]), "E2 0x80-0xBF 全是非法（续字节不能当首字节）")
    ok(all(v == S1 for v in FIRST[0xC0:0xE0]), "E2 0xC0-0xDF 是 s1（两字节）")
    eq(FIRST[0xE0], S2, "E2 0xE0 走 acceptRanges[1]")
    ok(all(v == S3 for v in FIRST[0xE1:0xED]), "E2 0xE1-0xEC 是 s3")
    eq(FIRST[0xED], S4, "E2 0xED 走 acceptRanges[2]（拦代理区）")
    ok(all(v == S3 for v in FIRST[0xEE:0xF0]), "E2 0xEE-0xEF 是 s3")
    eq(FIRST[0xF0], S5, "E2 0xF0 走 acceptRanges[3]")
    ok(all(v == S6 for v in FIRST[0xF1:0xF4]), "E2 0xF1-0xF3 是 s6")
    eq(FIRST[0xF4], S7, "E2 0xF4 走 acceptRanges[4]（拦超上限）")
    ok(all(v == XX for v in FIRST[0xF5:0x100]), "E2 0xF5-0xFF 全是非法")
    # 低 4 位就是字节长度（官方注释：The second nibble is the Rune length）
    for b, want in ((0x41, 0), (0xC2, 2), (0xE1, 3), (0xF1, 4)):
        ok(FIRST[b] & 7 == want if want else FIRST[b] == AS,
           "E2 first[0x%02X] 的低 4 位给出长度 %d" % (b, want))


# ---------------------------------------------------------------- E3 acceptRanges 拦住的三类非法
def e3_accept_ranges():
    eq(ACCEPT_RANGES[0], (LOCB, HICB), "E3 accept 0 默认区间")
    eq(ACCEPT_RANGES[1], (0xA0, HICB), "E3 accept 1：E0 后必须 >= 0xA0")
    eq(ACCEPT_RANGES[2], (LOCB, 0x9F), "E3 accept 2：ED 后必须 <= 0x9F")
    eq(ACCEPT_RANGES[3], (0x90, HICB), "E3 accept 3：F0 后必须 >= 0x90")
    eq(ACCEPT_RANGES[4], (LOCB, 0x8F), "E3 accept 4：F4 后必须 <= 0x8F")
    # overlong：E0 80 80（本该是两字节的 0）
    eq(tu(decode_rune([0xE0, 0x80, 0x80])), (RUNE_ERROR, 1), "E3 E0 80 80 overlong 非法")
    # 代理区：ED A0 80 = U+D800
    eq(tu(decode_rune([0xED, 0xA0, 0x80])), (RUNE_ERROR, 1), "E3 ED A0 80 代理区非法")
    eq(tu(decode_rune([0xED, 0x9F, 0xBF])), (0xD7FF, 3), "E3 ED 9F BF = U+D7FF 合法（边界内侧）")
    # 超上限：F4 90 80 80 = U+110000
    eq(tu(decode_rune([0xF4, 0x90, 0x80, 0x80])), (RUNE_ERROR, 1), "E3 F4 90 80 80 超上限非法")
    eq(tu(decode_rune([0xF4, 0x8F, 0xBF, 0xBF])), (0x10FFFF, 4), "E3 F4 8F BF BF = U+10FFFF 合法")
    # F0 overlong
    eq(tu(decode_rune([0xF0, 0x80, 0x80, 0x80])), (RUNE_ERROR, 1), "E3 F0 80 80 80 overlong 非法")
    eq(tu(decode_rune([0xF0, 0x90, 0x80, 0x80])), (0x10000, 4), "E3 F0 90 80 80 = U+10000 合法")


# ---------------------------------------------------------------- E4 与 Python codec 对拍（编码）
def e4_encode_crosscheck():
    bad = []
    for cp in range(0, MAX_RUNE + 1):
        if SURROGATE_MIN <= cp <= SURROGATE_MAX:
            continue
        got = bytes(encode_rune(cp))
        want = chr(cp).encode("utf-8")
        if got != want:
            bad.append((cp, got, want))
            if len(bad) > 5:
                break
    eq(bad, [], "E4 全量码点（除代理区）编码与 Python codec 逐字节一致")
    # 代理区：Go 写 RuneError，Python 用 surrogatepass 才写得出来
    eq(bytes(encode_rune(0xD800)), bytes(rune_error_bytes()), "E4 代理区写成 RuneError")
    eq(bytes(encode_rune(0x110000)), bytes(rune_error_bytes()), "E4 超上限写成 RuneError")
    eq(bytes(encode_rune(-1)), bytes(rune_error_bytes()), "E4 负数写成 RuneError")


# ---------------------------------------------------------------- E5 与 Python codec 对拍（解码）
def e5_decode_crosscheck():
    bad = []
    step = 97  # 步长采样式全量对拍
    for cp in range(0, MAX_RUNE + 1, step):
        if SURROGATE_MIN <= cp <= SURROGATE_MAX:
            continue
        raw = list(chr(cp).encode("utf-8"))
        r, size = tu(decode_rune(raw))
        if r != cp or size != len(raw):
            bad.append((cp, r, size))
            if len(bad) > 5:
                break
    eq(bad, [], "E5 采样码点解码与 Python codec 一致")
    # 非法输入：Go 返回 (RuneError, 1)，Python 直接抛异常 —— 差异是设计选择
    for raw in ([0x80], [0xFF], [0xC2], [0xE0, 0x80]):
        r, size = tu(decode_rune(raw))
        eq((r, size), (RUNE_ERROR, 1), "E5 非法序列 %s → (RuneError,1)" % raw)
        try:
            bytes(raw).decode("utf-8")
            ok(False, "E5 Python 本该对 %s 抛异常" % raw)
        except UnicodeDecodeError:
            ok(True, "E5 Python 对 %s 抛异常（Go 是替换字符）" % raw)
    # 空输入：size 是 0 不是 1（唯一的区别）
    eq(tu(decode_rune([])), (RUNE_ERROR, 0), "E5 空输入 size=0")
    # 截断：n < sz 时返回 (RuneError, 1)
    eq(tu(decode_rune([0xE4, 0xB8])), (RUNE_ERROR, 1), "E5 截断的三字节序列 size=1")


# ---------------------------------------------------------------- E6 RuneLen
def e6_rune_len():
    eq(rune_len(-1), -1, "E6 负数 -1")
    eq(rune_len(0), 1, "E6 0 → 1")
    eq(rune_len(0x7F), 1, "E6 U+007F → 1")
    eq(rune_len(0x80), 2, "E6 U+0080 → 2")
    eq(rune_len(0x7FF), 2, "E6 U+07FF → 2")
    eq(rune_len(0x800), 3, "E6 U+0800 → 3")
    eq(rune_len(0xD800), -1, "E6 代理区起点 -1")
    eq(rune_len(0xDFFF), -1, "E6 代理区终点 -1")
    eq(rune_len(0xD7FF), 3, "E6 代理区前一个仍是 3")
    eq(rune_len(0xE000), 3, "E6 代理区后一个仍是 3")
    eq(rune_len(0xFFFF), 3, "E6 U+FFFF → 3")
    eq(rune_len(0x10000), 4, "E6 U+10000 → 4")
    eq(rune_len(MAX_RUNE), 4, "E6 MaxRune → 4")
    eq(rune_len(MAX_RUNE + 1), -1, "E6 超上限 -1")
    # RuneLen 与 EncodeRune 的长度必须自洽（代理区除外）
    bad = [cp for cp in (1, 0x7F, 0x80, 0x7FF, 0x800, 0xFFFF, 0x10000, MAX_RUNE)
           if len(encode_rune(cp)) != rune_len(cp)]
    eq(bad, [], "E6 RuneLen 与 EncodeRune 长度自洽")


# ---------------------------------------------------------------- E7 ASCII 快路径
def e7_ascii_fast_path():
    for b in (0x00, 0x41, 0x7F):
        eq(tu(decode_rune([b])), (b, 1), "E7 0x%02X 单字节直返" % b)
    # 首字节是 ASCII 时，后面哪怕跟着非法字节也不看
    eq(tu(decode_rune([0x41, 0xFF, 0xFF])), (0x41, 1), "E7 ASCII 之后的内容不参与本次解码")


# ---------------------------------------------------------------- E8 RuneCountInString
def e8_rune_count():
    eq(rune_count_in_string(b"hello"), 5, "E8 纯 ASCII")
    s = "世界".encode("utf-8")
    eq(rune_count_in_string(s), 2, "E8 两个汉字 = 2 个 rune")
    eq(len(s), 6, "E8 但字节数是 6（len 是字节数不是 rune 数）")
    # 非法字节：每个非法字节按 1 个 rune 计
    eq(rune_count_in_string(b"\x80\x80"), 2, "E8 两个非法字节算 2 个 rune")
    eq(rune_count_in_string(b"a\xffb"), 3, "E8 a + 非法 + b = 3")
    # 截断序列只算 1（因为 size=1 就把游标推进一格）
    eq(rune_count_in_string(b"\xe4\xb8"), 2, "E8 截断序列按两个非法字节算")


# ---------------------------------------------------------------- E9 RuneStart / Valid
def e9_rune_start_valid():
    ok(rune_start(0x41), "E9 ASCII 字节是首字节")
    ok(rune_start(0xC2), "E9 0xC2 是首字节")
    ok(not rune_start(0x80), "E9 0x80 是续字节")
    ok(not rune_start(0xBF), "E9 0xBF 是续字节")
    ok(all(not rune_start(b) for b in range(0x80, 0xC0)), "E9 0x80-0xBF 全是续字节")
    ok(valid("hello".encode()), "E9 合法 ASCII")
    ok(valid("世界".encode("utf-8")), "E9 合法中文")
    ok(not valid(b"\xff"), "E9 0xFF 非法")
    ok(not valid(b"\xed\xa0\x80"), "E9 代理区非法")
    ok(not valid(b"\xc2"), "E9 截断非法")


def main():
    for fn in (e1_constants, e2_first_table, e3_accept_ranges,
               e4_encode_crosscheck, e5_decode_crosscheck, e6_rune_len,
               e7_ascii_fast_path, e8_rune_count, e9_rune_start_valid):
        fn()
    print("PASS=%d FAIL=%d" % (PASS[0], FAIL[0]))
    return 1 if FAIL[0] else 0


if __name__ == "__main__":
    sys.exit(main())
