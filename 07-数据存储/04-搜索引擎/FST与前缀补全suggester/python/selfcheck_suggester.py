"""FST 与前缀补全 suggester —— 自检。

期望值全部来自 NRTSuggester / ConcatenateGraphFilter / TokenStreamToAutomaton / Operations
源码实读 + 手算，且先实跑校准过一遍。

运行：python selfcheck_suggester.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from suggester import (encode, decode, write_vint, read_vint, PayLoadProcessor,
                       calculate_live_doc_ratio, get_max_top_n_queue_size,
                       SuggesterFST, SuggestLookup, TopNSearcher,
                       INT_MAX, MAX_TOP_N_QUEUE_SIZE, DEFAULT_PAYLOAD_SEP,
                       POS_SEP, HOLE, DEFAULT_MAX_GRAPH_EXPANSIONS)

PASS = 0
FAIL = []


def check(name, cond):
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(name)


def eq(name, got, want):
    check("%s (got=%r want=%r)" % (name, got, want), got == want)


def close(name, got, want, tol=1e-9):
    check("%s (got=%r want=%r)" % (name, got, want), abs(got - want) <= tol)


# ================================================== 1. 权重编码（越大越"小"）
eq("encode(0)", encode(0), INT_MAX)
eq("encode(10)", encode(10), INT_MAX - 10)
eq("decode(encode(7))", decode(encode(7)), 7)
check("权重越大 → 编码值越小", encode(100) < encode(10))
check("升序取 top-N 等价于权重降序", sorted([encode(5), encode(50), encode(500)])[0] == encode(500))
for bad in (-1, INT_MAX + 1):
    try:
        encode(bad)
        check("encode(%d) 应抛异常" % bad, False)
    except ValueError:
        check("encode(%d) 抛 ValueError" % bad, True)
try:
    decode(-1)
    check("decode(-1) 应断言失败", False)
except AssertionError:
    check("decode(-1) 触发断言", True)

# ============================================================ 2. vInt 编解码
eq("write_vint(0)", write_vint(0), b"\x00")
eq("write_vint(1)", write_vint(1), b"\x01")
eq("write_vint(127)", write_vint(127), b"\x7f")
eq("write_vint(128) 两字节", write_vint(128), b"\x80\x01")
eq("write_vint(16383)", write_vint(16383), b"\xff\x7f")
eq("write_vint(16384) 三字节", write_vint(16384), b"\x80\x80\x01")
for v in (0, 1, 127, 128, 16383, 16384, 1 << 21, INT_MAX):
    eq("vint 往返 %d" % v, read_vint(write_vint(v))[0], v)
eq("read_vint 返回新位置", read_vint(b"\x80\x01zz", 0), (128, 2))
eq("payload 里 docId 最多 6 字节(含 sep)",
   PayLoadProcessor.MAX_DOC_ID_LEN_WITH_SEP, 6)
try:
    read_vint(b"\xff\xff\xff\xff\xff\xff", 0)
    check("6 字节 continuation 应报错", False)
except ValueError:
    check("超长 vInt 抛 ValueError", True)

# ============================================================ 3. payload 布局
surface = b"coffee"
pl = PayLoadProcessor.make(surface, 42, DEFAULT_PAYLOAD_SEP)
eq("payload = surface + sep + vint(doc)",
   pl, surface + bytes([DEFAULT_PAYLOAD_SEP]) + write_vint(42))
got_s, idx = PayLoadProcessor.parse_surface_form(pl, DEFAULT_PAYLOAD_SEP)
eq("切出 surface", got_s, surface)
eq("分隔符下标", idx, len(surface))
eq("切出 docId", read_vint(pl, idx + 1)[0], 42)
# 找的是**第一个**分隔符，不是最后一个
pl2 = PayLoadProcessor.make(b"a\x1fb", 7, DEFAULT_PAYLOAD_SEP)
eq("只认第一个分隔符", PayLoadProcessor.parse_surface_form(pl2, DEFAULT_PAYLOAD_SEP)[0], b"a")
try:
    PayLoadProcessor.parse_surface_form(b"nosep", DEFAULT_PAYLOAD_SEP)
    check("无分隔符应断言失败", False)
except AssertionError:
    check("无分隔符触发断言", True)

# =============================================== 4. liveDocsRatio / queueSize
eq("numDocs=0 → -1", calculate_live_doc_ratio(0, 100), -1)
close("无删除 → 1.0", calculate_live_doc_ratio(100, 100), 1.0)
close("删了一半 → 0.5", calculate_live_doc_ratio(50, 100), 0.5)
# topN * maxAnalyzedPaths / ratio，无 filter
eq("queueSize 基础值", get_max_top_n_queue_size(10, 1000, 1.0, False, 5), 50)
eq("删一半 → 队列翻倍", get_max_top_n_queue_size(10, 1000, 0.5, False, 5), 100)
eq("开 filter → 再加 numDocs/2", get_max_top_n_queue_size(10, 1000, 1.0, True, 5), 50 + 500)
eq("封顶 5000", get_max_top_n_queue_size(10, 100000, 0.001, True, 5000), MAX_TOP_N_QUEUE_SIZE)
check("封顶值就是常量", MAX_TOP_N_QUEUE_SIZE == 5000)

# ============================================ 5. 分析链默认常量（源码实读）
eq("POS_SEP", POS_SEP, 0x001F)
eq("HOLE", HOLE, 0x001E)
eq("DEFAULT_MAX_GRAPH_EXPANSIONS = determinize work limit",
   DEFAULT_MAX_GRAPH_EXPANSIONS, 10000)
check("HOLE 与 POS_SEP 相邻且更小", HOLE == POS_SEP - 1)

# ======================================================== 6. FST 前缀匹配
fst = SuggesterFST()
for w, s, d in [(100, "coffee", 1), (50, "coffee bean", 2), (75, "caffeine", 3),
                (10, "tea", 4)]:
    fst.add(s, w, d)

eq("前缀 co 命中", len(fst.intersect_prefix_paths("co")), 1)
eq("前缀 ca 命中", len(fst.intersect_prefix_paths("ca")), 1)
eq("前缀 zz 不命中", len(fst.intersect_prefix_paths("zz")), 0)
eq("空前缀命中根", len(fst.intersect_prefix_paths("")), 1)

lk = SuggestLookup(fst, max_analyzed_paths_per_output=1)
# topN = countToCollect * prefixPaths.size()
eq("topN 乘以路径数", lk.plan(3, 100, 100)[1], 3)
eq("topN 随路径数放大", lk.plan(3, 100, 100, paths_per_output=4)[1], 12)
eq("numDocs=0 直接返回", lk.plan(3, 0, 100), (-1, 0, 0))

res = lk.lookup("co", 10, 100, 100)
eq("co 补全两条", len(res), 2)
eq("权重高的排前面", [r["surface"] for r in res], ["coffee", "coffee bean"])
eq("docId 回填正确", sorted(r["doc"] for r in res), [1, 2])
eq("weight 解码正确", res[0]["weight"], 100)
eq("查不到前缀返回空", lk.lookup("zz", 10, 100, 100), [])
eq("空 segment 返回空", lk.lookup("co", 10, 0, 100), [])

# top-N 截断
res2 = lk.lookup("co", 1, 100, 100)
eq("countToCollect=1 只留一条", len(res2), 1)
eq("留下的是最高权重", res2[0]["surface"], "coffee")

# 同权重时按 surface 字典序（ScoringPathComparator 的 tie-break）
fst2 = SuggesterFST()
fst2.add("zebra", 5, 1)
fst2.add("apple", 5, 2)
lk2 = SuggestLookup(fst2)
r3 = lk2.lookup("z", 10, 100, 100)
r4 = lk2.lookup("", 10, 100, 100)
eq("同权重按 surface 升序", [x["surface"] for x in r4], ["apple", "zebra"])

# ============================================ 7. 去重（skipDuplicates）
# 同一个 surface 有两条路径（不同 docId / 不同 context），dedup 只留分高的那条
fst3 = SuggesterFST()
fst3.add("coffee", 100, 1)
fst3.add("coffee", 20, 9)
lk3 = SuggestLookup(fst3)
no_dedup = lk3.lookup("cof", 10, 100, 100, dedup=False)
eq("不去重：同一 surface 出两条", len(no_dedup), 2)
eq("不去重：权重降序", [r["weight"] for r in no_dedup], [100, 20])

sr = TopNSearcher(fst3, 10, 50, dedup=True, payload_sep=DEFAULT_PAYLOAD_SEP)
sr.add_start_paths(fst3.intersect_prefix_paths("cof")[0])
deduped = sr.search()
eq("去重后只剩一条", len(deduped), 1)
eq("留下的是分高的那条", deduped[0]["doc"], 1)
check("去重确实剪了枝", sr.pruned is True)
snd = TopNSearcher(fst3, 10, 50, dedup=False, payload_sep=DEFAULT_PAYLOAD_SEP)
snd.add_start_paths(fst3.intersect_prefix_paths("cof")[0])
eq("未去重：两条都收", len(snd.search()), 2)

# ==================================================== 8. 端到端：boost 影响
check("score = weight * boost(默认 1.0)", all(abs(r["score"] - r["weight"]) < 1e-9 for r in res))
s2 = TopNSearcher(fst, 10, 50, payload_sep=DEFAULT_PAYLOAD_SEP)
s2.boost = 2.0
s2.add_start_paths(fst.intersect_prefix_paths("co")[0])
r5 = s2.search()
eq("boost=2 时最高分翻倍", r5[0]["score"], 200.0)
eq("boost 不改变排序", [x["surface"] for x in r5], ["coffee", "coffee bean"])

print("PASS=%d FAIL=%d" % (PASS, len(FAIL)))
for f in FAIL:
    print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
