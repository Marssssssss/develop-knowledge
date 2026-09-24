"""Lucene 倒排表压缩与跳表自检。"""

import random
import sys

from forutil import (collapse16, collapse8, expand16, expand8, forutil_decode,
                     forutil_encode_bytes, mask16, mask8, num_bytes,
                     placement_map, primitive_size_of)
from pfor import (BLOCK_SIZE, MAX_EXCEPTIONS, all_equal, bits_required,
                  pfor_decode, pfor_encode, pfor_skip)
from skiplist import (LEVEL1_FACTOR, LEVEL1_MASK, LEVEL1_NUM_DOCS,
                      SkipListWriter, buffer_skip_levels, entries_per_level,
                      level1_group_of, level1_offset_in_group, log,
                      number_of_skip_levels, skip_to)

PASSED = 0
FAILED = []


def ok(name, cond):
    global PASSED
    if cond:
        PASSED += 1
    else:
        FAILED.append(name)
        print("  FAIL %s" % name)


def eq(name, got, want):
    ok("%s (got %r want %r)" % (name, got, want), got == want)


def t_bits_required():
    eq("bits_required(0)", bits_required(0), 0)
    eq("bits_required(1)", bits_required(1), 1)
    eq("bits_required(255)", bits_required(255), 8)
    eq("bits_required(256)", bits_required(256), 9)
    eq("bits_required(300)", bits_required(300), 9)


def t_masks():
    eq("mask8(0)", mask8(0), 0)
    eq("mask8(1)", mask8(1), 0x01010101)
    eq("mask8(3)", mask8(3), 0x07070707)
    eq("mask8(7)", mask8(7), 0x7F7F7F7F)
    eq("mask16(1)", mask16(1), 0x00010001)
    eq("mask16(16)", mask16(16), 0xFFFFFFFF)


def t_collapse():
    a = list(range(256))
    collapse8(a)
    eq("collapse8 work[0]", a[0], (0 << 24) | (64 << 16) | (128 << 8) | 192)
    eq("collapse8 work[63]", a[63], (63 << 24) | (127 << 16) | (191 << 8) | 255)
    expand8(a)
    eq("expand8 还原", a, list(range(256)))

    b = list(range(256))
    collapse16(b)
    eq("collapse16 work[0]", b[0], (0 << 16) | 128)
    eq("collapse16 work[127]", b[127], (127 << 16) | 255)
    expand16(b)
    eq("expand16 还原", b, list(range(256)))


def t_num_bytes():
    eq("num_bytes(1)", num_bytes(1), 32)
    eq("num_bytes(3)", num_bytes(3), 96)
    eq("num_bytes(8)", num_bytes(8), 256)
    eq("num_bytes(16)", num_bytes(16), 512)
    eq("num_bytes(17)", num_bytes(17), 544)
    eq("num_bytes(32)", num_bytes(32), 1024)
    eq("primitive_size_of(8)", primitive_size_of(8), 8)
    eq("primitive_size_of(9)", primitive_size_of(9), 16)
    eq("primitive_size_of(17)", primitive_size_of(17), 32)


def t_forutil_roundtrip():
    for bpv in range(1, 33):
        vals = [random.Random(bpv).randrange(0, 1 << bpv) for _ in range(256)]
        blob = forutil_encode_bytes(vals, bpv)
        words = [int.from_bytes(blob[i:i + 4], "big")
                 for i in range(0, len(blob), 4)]
        ok("forutil bpv=%d 往返" % bpv, forutil_decode(words, bpv) == vals)
        ok("forutil bpv=%d 字节数" % bpv, len(blob) == num_bytes(bpv))


def t_forutil_layout():
    vals = list(range(256))
    b8 = forutil_encode_bytes(vals, 8)
    eq("bpv=8 前 4 字节是 4 条泳道的第 0 个值", list(b8[:4]), [0, 64, 128, 192])
    eq("bpv=8 第 5-8 字节是各泳道第 1 个值", list(b8[4:8]), [1, 65, 129, 193])
    b16 = forutil_encode_bytes(vals, 16)
    eq("bpv=16 前 4 字节（2 泳道，大端 16 位）", list(b16[:4]),
       [0, 0, 0, 128])
    eq("bpv=16 第 5-8 字节", list(b16[4:8]), [0, 1, 0, 129])


def t_forutil_extremes():
    zeros = [0] * 256
    blob = forutil_encode_bytes(zeros, 3)
    eq("全 0 的 bpv=3 块长度", len(blob), 96)
    eq("全 0 的 bpv=3 块全零", set(blob), {0})
    ones = [(1 << 3) - 1] * 256
    blob = forutil_encode_bytes(ones, 3)
    eq("全 7 的 bpv=3 块全 0xFF", set(blob), {0xFF})


def t_pfor_basic():
    blk = [1] * 256
    enc, d = pfor_encode(blk)
    eq("全 1 max_bits", d["max_bits"], 1)
    eq("全 1 patched_bits", d["patched_bits"], 1)
    eq("全 1 exceptions", d["num_exceptions"], 0)
    ok("全 1 走 allEqual 分支", d["all_equal"])
    eq("全 1 长度（token 加 vint）", len(enc), 2)
    eq("全 1 token", enc[0], 0)
    ok("全 1 往返", pfor_decode(enc) == blk)

    z = [0] * 256
    enc, d = pfor_encode(z)
    eq("全 0 max_bits", d["max_bits"], 0)
    eq("全 0 长度", len(enc), 2)
    eq("全 0 内容", list(enc), [0, 0])
    ok("全 0 往返", pfor_decode(enc) == z)

    sevens = [7] * 256
    enc, d = pfor_encode(sevens)
    eq("全 7 patched_bits", d["patched_bits"], 3)
    eq("全 7 长度", len(enc), 2)
    ok("全 7 往返", pfor_decode(enc) == sevens)


def t_pfor_outlier():
    # 255 个 1（1 位）加 1 个 300（9 位）：例外只有 1 个，位数一路压到 1
    blk = [1] * 255 + [300]
    enc, d = pfor_encode(blk)
    eq("离群 max_bits", d["max_bits"], 9)
    eq("离群 patched_bits", d["patched_bits"], 1)
    eq("离群 exceptions", d["num_exceptions"], 1)
    ok("离群不落 allEqual", not d["all_equal"])
    eq("离群块长度（token 加 numBytes(1) 加 2×1）", len(enc), 1 + 32 + 2)
    eq("离群 token 高位是 1 个例外", enc[0] >> 5, 1)
    eq("离群 token 低位是 1 位", enc[0] & 0x1F, 1)
    ok("离群往返", pfor_decode(enc) == blk)
    eq("离群 patch 下标", enc[-2], 255)
    eq("离群 patch 高位（300>>1）", enc[-1], 150)


def t_pfor_multi_exceptions():
    # 250 个 1（1 位）加 6 个 5（3 位）：hist[3]=6 ≤ 7，会把位数压到 2 再压到 1
    blk = [1] * 250 + [5] * 6
    enc, d = pfor_encode(blk)
    eq("多例外 max_bits", d["max_bits"], 3)
    eq("多例外 patched_bits", d["patched_bits"], 1)
    eq("多例外 exceptions", d["num_exceptions"], 6)
    ok("多例外往返", pfor_decode(enc) == blk)
    # 六个 5 被压成 1 之后整块全等，于是走 allEqual 分支：token 加 vInt 加 patch
    ok("多例外掩码后整块全等", d["all_equal"])
    eq("多例外长度", len(enc), 1 + 1 + 12)
    eq("多例外 skip", pfor_skip(enc), len(enc))


def t_pfor_patch_byte_limit():
    # patch 只占 1 字节，位数最多下调 8：max_bits=12 时下限是 4
    blk = [7] * 255 + [4000]
    enc, d = pfor_encode(blk)
    eq("下调上限 max_bits", d["max_bits"], 12)
    ok("下调不低于 max_bits 减 8", d["patched_bits"] >= d["max_bits"] - 8)
    ok("4000 往返", pfor_decode(enc) == blk)
    for i in range(d["num_exceptions"]):
        idx = enc[1 + num_bytes(d["patched_bits"]) + 2 * i]
        hi = enc[1 + num_bytes(d["patched_bits"]) + 2 * i + 1]
        ok("patch 高位不超过 1 字节", 0 <= hi <= 255)


def t_pfor_random_roundtrip():
    for seed in range(6):
        rnd = random.Random(seed)
        blk = [rnd.randrange(0, 1 << rnd.randrange(1, 20))
               for _ in range(BLOCK_SIZE)]
        enc, d = pfor_encode(blk)
        ok("随机块 seed=%d 往返" % seed, pfor_decode(enc) == blk)
        ok("随机块 seed=%d skip 对齐" % seed, pfor_skip(enc) == len(enc))
        ok("随机块 seed=%d 例外不超过 %d" % (seed, MAX_EXCEPTIONS),
           d["num_exceptions"] <= MAX_EXCEPTIONS)


def t_placement_invariants():
    for bpv in range(1, 33):
        ps = primitive_size_of(bpv)
        num_ints = 256 * ps // 32
        pl = placement_map(bpv)
        eq("bpv=%d 值字个数" % bpv, len(pl), num_ints)
        used = {}
        for i, spots in enumerate(pl):
            eq("bpv=%d 值 %d 位数" % (bpv, i), len(spots), bpv)
            for w, fb in spots:
                used[(w, fb)] = used.get((w, fb), 0) + 1
        ok("bpv=%d 无重复落点" % bpv, all(v == 1 for v in used.values()))
        eq("bpv=%d 落点总数" % bpv, len(used), num_ints * bpv)
        eq("bpv=%d 用到的字数是 bpv×8" % bpv,
           max(w for w, _ in used) + 1, bpv * 8)


def t_skip_levels():
    eq("df 不超过 skipInterval 只有 1 层",
       number_of_skip_levels(100, 128, 8, 10), 1)
    eq("df=128 仍只有 1 层", number_of_skip_levels(128, 128, 8, 10), 1)
    eq("df=1000 时 floor(df/128)=7 不够 8 的 1 次方，仍 1 层",
       number_of_skip_levels(1000, 128, 8, 10), 1)
    eq("df=100000 时 4 层", number_of_skip_levels(100000, 128, 8, 10), 4)
    eq("被 maxSkipLevels 截断", number_of_skip_levels(100000, 128, 8, 2), 2)
    eq("log(8, 781)", log(8, 781), 3)
    eq("log(8, 7)", log(8, 7), 0)
    eq("log(8, 512)", log(8, 512), 3)


def t_buffer_skip():
    # windowLength = skipInterval * skipMultiplier = 128 * 8 = 1024
    eq("df=128 只写 0 层", buffer_skip_levels(128, 128, 8, 4), [0])
    eq("df=1024 写 0、1 层", buffer_skip_levels(1024, 128, 8, 4), [0, 1])
    eq("df=8192 写 0、1、2 层", buffer_skip_levels(8192, 128, 8, 4), [0, 1, 2])
    eq("df=8192 但只有 2 层时封顶", buffer_skip_levels(8192, 128, 8, 2), [0, 1])
    eq("level 0 条目数", entries_per_level(100000, 128, 8, 0), 781)
    eq("level 1 条目数", entries_per_level(100000, 128, 8, 1), 97)
    eq("level 2 条目数", entries_per_level(100000, 128, 8, 2), 12)


def t_skip_writer():
    w = SkipListWriter(100000, 128, 8, 10)
    eq("100000 篇文档的层数", w.num_levels, 4)
    w.feed(range(100000))
    sizes = w.level_sizes()
    eq("level 0 的 datum 数", sizes[0], 781)
    ok("层级越高条目越少", sizes[0] > sizes[1] > sizes[2] >= sizes[3])
    eq("落盘自高层向低层", w.write_order(), [3, 2, 1, 0])
    ok("1 层以上才有 child pointer",
       len(w.child_pointers[0]) == 0 and len(w.child_pointers[1]) > 0)


def t_skip_reader():
    # 三层，各层当前 skip datum 指向的文档号
    lvl_docs = [128, 1024, 8192]
    eq("target 不超过 level 1 当前 datum 时停在 level 0",
       skip_to(500, lvl_docs, 3), (0, 128))
    eq("target 超过 level 1 但不超过 level 2", skip_to(2000, lvl_docs, 3),
       (1, 1024))
    eq("target 超过 level 2 时到顶", skip_to(10000, lvl_docs, 3), (2, 8192))
    eq("只有 1 层时永远停在 level 0", skip_to(10000, lvl_docs, 1), (0, 128))


def t_lucene104_two_level():
    # Lucene104PostingsFormat 自己实现两级跳表：LEVEL1_FACTOR=32 个 256-块
    eq("LEVEL1_FACTOR", LEVEL1_FACTOR, 32)
    eq("LEVEL1_NUM_DOCS", LEVEL1_NUM_DOCS, 8192)
    eq("LEVEL1_MASK", LEVEL1_MASK, 8191)
    eq("文档 0 在第 0 组", level1_group_of(0), 0)
    eq("文档 8191 仍在第 0 组", level1_group_of(8191), 0)
    eq("文档 8192 进入第 1 组", level1_group_of(8192), 1)
    eq("组内偏移用掩码", level1_offset_in_group(8193), 1)
    eq("组内偏移与取模一致",
       level1_offset_in_group(123456), 123456 % LEVEL1_NUM_DOCS)


def main():
    t_bits_required()
    t_masks()
    t_collapse()
    t_num_bytes()
    t_forutil_roundtrip()
    t_forutil_layout()
    t_forutil_extremes()
    t_pfor_basic()
    t_pfor_outlier()
    t_pfor_multi_exceptions()
    t_pfor_patch_byte_limit()
    t_pfor_random_roundtrip()
    t_placement_invariants()
    t_skip_levels()
    t_buffer_skip()
    t_skip_writer()
    t_skip_reader()
    t_lucene104_two_level()
    print("通过 %d 项，失败 %d 项" % (PASSED, len(FAILED)))
    if FAILED:
        for f in FAILED:
            print("  - %s" % f)
        sys.exit(1)


if __name__ == "__main__":
    main()
