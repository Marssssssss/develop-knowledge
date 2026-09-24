"""Lucene 倒排表压缩与跳表 —— 演示入口。"""

import random

from forutil import (collapse8, forutil_encode_bytes, num_bytes,
                     primitive_size_of)
from pfor import pfor_decode, pfor_encode, pfor_skip
from skiplist import (SkipListWriter, buffer_skip_levels,
                      number_of_skip_levels, skip_to)


def show(title):
    print("\n== %s ==" % title)


def main():
    show("1. 位宽直方决定块位数（PForUtil）")
    for desc, blk in (("256 个 1", [1] * 256),
                      ("255 个 1 加 1 个 300", [1] * 255 + [300]),
                      ("250 个 1 加 6 个 5", [1] * 250 + [5] * 6),
                      ("4000 离群", [7] * 255 + [4000])):
        enc, d = pfor_encode(blk)
        print("   %-18s max_bits=%2d -> 压到 %2d 位, 例外 %d 个, "
              "块长 %4d 字节%s"
              % (desc, d["max_bits"], d["patched_bits"], d["num_exceptions"],
                 len(enc), "（整块全等，走 vInt 捷径）" if d["all_equal"] else ""))
        assert pfor_decode(enc) == blk
        assert pfor_skip(enc) == len(enc)

    show("2. 朴素存储 vs PForDelta")
    rnd = random.Random(7)
    doc_deltas = [1 + rnd.randrange(0, 40) for _ in range(256)]
    enc, d = pfor_encode(doc_deltas)
    print("   原始 int32 定长存储：%d 字节" % (256 * 4))
    print("   PForDelta         ：%d 字节（%d 位 + %d 个例外）"
          % (len(enc), d["patched_bits"], d["num_exceptions"]))
    print("   往返一致:", pfor_decode(enc) == doc_deltas)

    show("3. ForUtil 的泳道转置（bpv=8，值为下标）")
    blob = forutil_encode_bytes(list(range(256)), 8)
    print("   前 8 个字节:", list(blob[:8]))
    print("   -> 不是 0,1,2,3,...，而是 4 条泳道各取一个值交错")

    show("4. 块字节数只由位数决定")
    for bpv in (1, 3, 8, 16, 17, 32):
        print("   bpv=%2d -> primitiveSize=%2d, %4d 字节"
              % (bpv, primitive_size_of(bpv), num_bytes(bpv)))

    show("5. 多级跳表层数（skipInterval=128, skipMultiplier=8）")
    for df in (100, 128, 1000, 10000, 100000):
        print("   df=%7d -> %d 层"
              % (df, number_of_skip_levels(df, 128, 8, 10)))

    show("6. bufferSkip 决定这一条 datum 写进哪几层")
    for df in (128, 1024, 8192, 65536):
        print("   df=%6d -> 层级 %s"
              % (df, buffer_skip_levels(df, 128, 8, 4)))

    show("7. 一次真实跳表的层级分布（df=100000）")
    w = SkipListWriter(100000, 128, 8, 10).feed(range(100000))
    print("   层数:", w.num_levels, " 各层 datum 数:", w.level_sizes())
    print("   落盘顺序（自高层向低层）:", w.write_order())

    show("8. skipTo 先上爬到「够得着 target」的最高一层")
    lvl_docs = [128, 1024, 8192]
    for target in (500, 2000, 10000):
        level, doc = skip_to(target, lvl_docs, 3)
        print("   target=%6d -> level %d, 该层 datum 指向 %d"
              % (target, level, doc))

    show("9. collapse8 把 256 个值交叠进 64 个字")
    vals = list(range(256))
    work = list(vals)
    collapse8(work)
    print("   work[0] = 0x%08X （= v[0]<<24 | v[64]<<16 | v[128]<<8 | v[192]）"
          % work[0])
    print("   work[63]= 0x%08X" % work[63])


if __name__ == "__main__":
    main()
