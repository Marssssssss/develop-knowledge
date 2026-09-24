"""FST 与前缀补全 suggester —— 演示入口。

演示三件事：
  1. 权重为什么要用 Integer.MAX_VALUE - w 反过来存（升序 top-N = 权重降序）
  2. payload 为什么是 surface + sep + vint(docId)（怎么切回来）
  3. topN 与队列容量那两个"拍脑袋"的启发式是怎么算的
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from suggester import (encode, decode, write_vint, read_vint, PayLoadProcessor,
                       calculate_live_doc_ratio, get_max_top_n_queue_size,
                       SuggesterFST, SuggestLookup, INT_MAX, MAX_TOP_N_QUEUE_SIZE,
                       DEFAULT_PAYLOAD_SEP, POS_SEP, HOLE,
                       DEFAULT_MAX_GRAPH_EXPANSIONS)


def main():
    print("=" * 72)
    print("Lucene completion suggester（FST）演示")
    print("=" * 72)

    print("\n[1] 权重编码：encode(w) = Integer.MAX_VALUE - w")
    for w in (0, 10, 100, 1000):
        print("    w=%-5d → output1=%-11d  解码回 %d" % (w, encode(w), decode(encode(w))))
    smallest = sorted([(encode(w), w) for w in (5, 50, 500)])[0]
    print("    升序取最小 output1=%d ⇒ 对应权重 %d（三者里最大）"
          % (smallest[0], smallest[1]))

    print("\n[2] payload 布局：surface + PAYLOAD_SEP + vint(docId)")
    pl = PayLoadProcessor.make(b"coffee", 42, DEFAULT_PAYLOAD_SEP)
    print("    make(b'coffee', 42) = %r" % pl)
    s, idx = PayLoadProcessor.parse_surface_form(pl, DEFAULT_PAYLOAD_SEP)
    print("    切回 surface=%r  sepIdx=%d  docId=%d" % (s, idx, read_vint(pl, idx + 1)[0]))
    print("    vint 长度：0→%d B  127→%d B  128→%d B  16384→%d B（最多 5）"
          % (len(write_vint(0)), len(write_vint(127)),
             len(write_vint(128)), len(write_vint(16384))))
    print("    MAX_DOC_ID_LEN_WITH_SEP = %d"
          % PayLoadProcessor.MAX_DOC_ID_LEN_WITH_SEP)

    print("\n[3] 分析期常量（源码实读）")
    print("    POS_SEP=0x%04X  HOLE=0x%04X  DEFAULT_MAX_GRAPH_EXPANSIONS=%d"
          % (POS_SEP, HOLE, DEFAULT_MAX_GRAPH_EXPANSIONS))

    print("\n[4] 队列容量启发式（源码自称 simple heuristics，不保证 admissible）")
    for ratio, filt, nd in ((1.0, False, 1000), (0.5, False, 1000), (1.0, True, 1000)):
        q = get_max_top_n_queue_size(10, nd, ratio, filt, 5)
        print("    liveRatio=%.1f filter=%-5s numDocs=%d → queueSize=%d"
              % (ratio, filt, nd, q))
    print("    封顶 MAX_TOP_N_QUEUE_SIZE = %d" % MAX_TOP_N_QUEUE_SIZE)

    fst = SuggesterFST()
    for w, s, d in [(100, "coffee", 1), (80, "coffee bean", 2),
                    (60, "coffee maker", 3), (75, "caffeine", 4)]:
        fst.add(s, w, d)
    lk = SuggestLookup(fst, max_analyzed_paths_per_output=1)

    print("\n[5] 前缀补全")
    print("    liveRatio(0 存活) = %s  → lookup 直接 return"
          % calculate_live_doc_ratio(0, 100))
    r, top_n, q = lk.plan(3, 100, 100)
    print("    计划：liveRatio=%.2f topN=%d queueSize=%d" % (r, top_n, q))
    print("    topN 会乘以相交路径数（一个 suggestion 多个 context ⇒ 多条路径）：")
    for n in (1, 2, 4):
        print("      prefixPaths=%d → topN=%d" % (n, lk.plan(3, 100, 100, paths_per_output=n)[1]))

    for prefix in ("co", "cof", "ca", "zz"):
        hits = lk.lookup(prefix, 10, 100, 100)
        print("\n    前缀 %-4r → %d 条" % (prefix, len(hits)))
        for h in hits:
            print("        w=%-4d doc=%d  %s" % (h["weight"], h["doc"], h["surface"]))

    print("\n[6] 去重（skipDuplicates）")
    fst2 = SuggesterFST()
    fst2.add("coffee", 100, 1)
    fst2.add("coffee", 20, 9)
    lk2 = SuggestLookup(fst2)
    print("    同一 surface 两条路径：不去重 %d 条，去重后 %d 条"
          % (len(lk2.lookup("cof", 10, 100, 100, dedup=False)),
             len(lk2.lookup("cof", 10, 100, 100, dedup=True))))

    print("\n" + "=" * 72)


if __name__ == "__main__":
    main()
