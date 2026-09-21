"""列式存储与 Parquet 自检 —— 断言全部来自 parquet-format 官方规范与 Dremel 论文 Figure 3。

运行： python selfcheck_parquet.py
"""

import sys

from main import (
    REPEATED, assemble_column, bit_width, data_page, defcounts, document_schema,
    max_definition_level, max_repetition_level, pack_lsb_first, read_uleb128,
    repeated_index, rle_decode, rle_encode, shred_column, uleb128, unpack_lsb_first,
)

PASS = FAIL = 0


def ok(c, m):
    global PASS, FAIL
    if c:
        PASS += 1
    else:
        FAIL += 1
        print("FAIL:", m)


def eq(g, w, m):
    ok(g == w, "%s (got=%r want=%r)" % (m, g, w))


# Dremel 论文 Figure 2 的两篇文档（Parquet 官方示例同构）
DOC1 = {
    "DocId": 10,
    "Links": {"Backward": [10, 30], "Forward": [20, 40, 60]},
    "Name": [
        {"Language": [{"Code": "en-us", "Country": "us"},
                      {"Code": "en", "Country": None}],
         "Url": "http://A"},
        {"Url": "http://B"},
        {"Language": [{"Code": "en-gb", "Country": "gb"}], "Url": None},
    ],
}
DOC2 = {
    "DocId": 20,
    "Links": {"Forward": [80]},
    "Name": [{"Url": "http://C"}],
}
DOCS = [DOC1, DOC2]

SCHEMA = document_schema()


def path(*names):
    """取从根往下到该列的节点链（去掉根节点本身，它是 required 且不计入层级）。"""
    return SCHEMA.find(("Document",) + names)[1:]


# --------------------------------------------------- A 最大层级（schema 推导）
def t_levels():
    eq(max_definition_level(path("DocId")), 0, "A1 DocId 是 required 且非嵌套 → max d = 0")
    eq(max_repetition_level(path("DocId")), 0, "A2 DocId 无 repeated 祖先 → max r = 0")

    p = path("Links", "Backward")
    eq(max_definition_level(p), 2, "A3 Links(optional) + Backward(repeated) → max d = 2")
    eq(max_repetition_level(p), 1, "A4 路径上一个 repeated → max r = 1")

    p = path("Name", "Language", "Code")
    eq(max_definition_level(p), 2, "A5 Name + Language 是 repeated，Code 是 required → max d = 2")
    eq(max_repetition_level(p), 2, "A6 Name 与 Language 都是 repeated → max r = 2")

    p = path("Name", "Language", "Country")
    eq(max_definition_level(p), 3, "A7 Country 额外是 optional → max d = 3")
    eq(max_repetition_level(p), 2, "A8 max r 仍是 2")

    p = path("Name", "Url")
    eq(max_definition_level(p), 2, "A9 Name(repeated) + Url(optional) → max d = 2")
    eq(max_repetition_level(p), 1, "A10 max r = 1")

    # defcounts 决定「某一层是否被定义」
    eq(defcounts(path("Name", "Language", "Country")), [1, 2, 3], "A11 defcounts 逐层累加")
    eq(repeated_index(path("Name", "Language", "Country")), {0: 1, 1: 2}, "A12 repeated 的序号")


# ------------------------------------------- B 切分（Dremel Figure 3 复现）
def pairs(entries):
    return [(r, d) for _, r, d in entries]


def t_shred():
    # B1 DocId：required 且非嵌套，d 与 r 恒为 0
    e = shred_column(DOCS, path("DocId"))
    eq(pairs(e), [(0, 0), (0, 0)], "B1 DocId 两行 (r=0,d=0)")
    eq([v for v, _, _ in e], [10, 20], "B1b DocId 取值")

    # B2 Links.Backward：第二篇文档没有 Backward → NULL 且 d=1（Links 存在但 Backward 空）
    e = shred_column(DOCS, path("Links", "Backward"))
    eq(pairs(e), [(0, 2), (1, 2), (0, 1)], "B2 Backward 的 (r,d) 序列")
    eq([v for v, _, _ in e], [10, 30, None], "B2b 第三项是 NULL")

    # B3 Links.Forward
    e = shred_column(DOCS, path("Links", "Forward"))
    eq(pairs(e), [(0, 2), (1, 2), (1, 2), (0, 2)], "B3 Forward 的 (r,d) 序列")

    # B4 Name.Url
    e = shred_column(DOCS, path("Name", "Url"))
    eq(pairs(e), [(0, 2), (1, 2), (1, 1), (0, 2)], "B4 Url 的 (r,d) 序列")
    eq([v for v, _, _ in e], ["http://A", "http://B", None, "http://C"], "B4b 第三个 Name 没有 Url")

    # B5 Name.Language.Country —— Dremel 论文里的经典序列
    e = shred_column(DOCS, path("Name", "Language", "Country"))
    eq(pairs(e), [(0, 3), (2, 2), (1, 1), (1, 3), (0, 1)], "B5 Country 的 (r,d) 序列")
    eq([v for v, _, _ in e], ["us", None, None, "gb", None], "B5b Country 取值")

    # B6 Name.Language.Code：Code 本身是 required，但**祖先缺失时这一列照样要写
    #    一条 NULL**（required 并不代表列里没有 NULL，只代表它自己不会「缺失」）
    e = shred_column(DOCS, path("Name", "Language", "Code"))
    eq([v for v, _, _ in e], ["en-us", "en", None, "en-gb", None], "B6 Code 列含两个 NULL")
    eq(pairs(e), [(0, 2), (2, 2), (1, 1), (1, 2), (0, 1)], "B6b Code 的 (r,d) 序列")


# ------------------------------------------------------- C 装配（逆运算）
def t_assemble():
    p = path("Name", "Language", "Country")
    e = shred_column(DOCS, p)
    got = assemble_column(e, p)
    eq(got[0]["Name"][0]["Language"][0]["Country"], "us", "C1 第一个 Country 还原")
    eq(got[0]["Name"][0]["Language"][1], {}, "C2 第二个 Language 没有 Country 键")
    eq(got[0]["Name"][1], {}, "C3 第二个 Name 没有 Language 键")
    eq(got[0]["Name"][2]["Language"][0]["Country"], "gb", "C4 第三个 Name 的 Country")
    eq(got[1]["Name"][0], {}, "C5 第二篇文档的 Name 没有 Language")

    # C6 反复切分-装配-再切分必须稳定（幂等性）
    again = shred_column(got, p)
    eq(pairs(again), pairs(e), "C6 切分-装配-再切分是幂等的")

    # C7 重复叶子也能还原
    p2 = path("Links", "Backward")
    e2 = shred_column(DOCS, p2)
    got2 = assemble_column(e2, p2)
    eq(got2[0]["Links"]["Backward"], [10, 30], "C7 重复叶子还原成列表")
    ok("Backward" not in got2[1].get("Links", {}), "C7b 缺失的 repeated 不建空列表")

    # C8 单列装配出来的文档数等于原文档数（r=0 是文档边界）
    eq(len(got), 2, "C8 r=0 是文档边界，装配出 2 个文档")


# ------------------------------------------ D RLE / Bit-Packing Hybrid
def t_rle():
    # D1 官方 Encodings.md 给出的位打包示例：1..7 用 bit width 3 → 0x88 0xC6 0xFA
    vals = [0, 1, 2, 3, 4, 5, 6, 7]
    eq(pack_lsb_first(vals, 3), b"\x88\xc6\xfa", "D1 LSB-first 位打包与官方示例逐字节一致")
    eq(unpack_lsb_first(b"\x88\xc6\xfa", 3, 8), vals, "D1b 解包还原")

    # D2 varint 是 ULEB-128
    eq(uleb128(0), b"\x00", "D2a 0 的 ULEB128")
    eq(uleb128(127), b"\x7f", "D2b 127 单字节")
    eq(uleb128(128), b"\x80\x01", "D2c 128 需要两字节")
    eq(read_uleb128(b"\x80\x01", 0), (128, 2), "D2d 读回 128")

    # D3 bit-packed run 的 header 是 (len/8 << 1 | 1)，最低位为 1
    eq(uleb128((8 // 8) << 1 | 1), b"\x03", "D3a 8 个值的 bit-packed header")
    # D4 RLE run 的 header 是 (len << 1)，最低位为 0
    eq(uleb128(1000 << 1), uleb128(2000), "D4 RLE header 是最低位为 0 的偶数")

    # D5 长游程走 RLE：1000 个 0（官方：非嵌套列的 1000 个 NULL 就是这么存的）
    levels = [0] * 1000
    enc = rle_encode(levels, 1)
    ok(len(enc) < 20, "D5a RLE 让 1000 个同级值只占几个字节(%d)" % len(enc))
    eq(rle_decode(enc, 1, 1000), levels, "D5b 解码还原 1000 个 0")

    # D6 混合序列往返
    seq = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 2, 1, 0, 0, 3, 3, 3, 3]
    enc = rle_encode(seq, 2)
    eq(rle_decode(enc, 2, len(seq)), seq, "D6 混合 run 往返一致")

    # D7 不足 8 个的尾部会被补 0 后按 8 打包（官方：总是 8 的倍数）
    short = [1, 2, 3]
    eq(rle_decode(rle_encode(short, 2), 2, 3), short, "D7 尾部不足 8 个也能还原（补 0）")

    # D8 bit_width 由最大层级决定
    eq(bit_width(3), 2, "D8a max d = 3 需要 2 bit")
    eq(bit_width(0), 1, "D8b 0 也要 1 bit（不能是 0）")


# ------------------------------------------------------- E 数据页布局
def t_page():
    # E1 required 且非嵌套的列：只有 values 段（rep 与 def 都省掉）
    page = data_page(None, None, [b"\x01\x02"], 0, 0)
    eq(page, b"V\x01\x02", "E1 非嵌套 required 列只有 values")

    # E2 可选列：只有 def 段
    page = data_page(None, [0, 1], [b"\x09"], 0, 1)
    ok(page.startswith(b"D"), "E2a 非嵌套列不写 rep 段")
    ok(b"V" in page, "E2b values 段在最后")

    # E3 嵌套列：rep 段在 def 段之前
    page = data_page([0, 1, 2], [3, 3, 2], [b"a", b"b"], 2, 2)
    ok(page.index(b"R") < page.index(b"D") < page.index(b"V"),
       "E3 数据页顺序是 rep → def → values")

    # E4 NULL 不占 values：1000 个 NULL 的页里 values 段为空
    page = data_page(None, [0] * 1000, [], 0, 1)
    ok(page.endswith(b"V"), "E4a 没有值可写时 values 段为空")
    ok(len(page) < 32, "E4b 1000 个 NULL 的整页只有几十字节")


def main():
    t_levels(); t_shred(); t_assemble(); t_rle(); t_page()
    print("PASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
