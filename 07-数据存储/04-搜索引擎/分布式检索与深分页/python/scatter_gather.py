#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ES 分布式检索两阶段(query/fetch)、深分页与 search_type 的最小模型。

权威来源(实际读过,不凭记忆):
  1. https://www.elastic.co/guide/en/elasticsearch/reference/current/search-search.html
     - search_type 合法值只有 **query_then_fetch** 与 **dfs_query_then_fetch**:
       query_then_fetch 用**分片本地**的 term/document frequency 打分(更快但不那么准);
       dfs_query_then_fetch 用**跨分片全局**频率打分(更慢但更准)
     - allow_partial_search_results: true ⇒ 有分片超时/失败时**返回部分结果**;
       false ⇒ 报错且不返回部分结果
     - batched_reduce_size:协调节点一次归并多少个分片结果
     - max_concurrent_shard_requests 默认 **5**
     - preference:默认用 **adaptive replica selection**(并考虑 allocation awareness)挑副本;
       可选 _only_local / _local / _only_nodes / _prefer_nodes / _shards:<id> / 自定义串
     - routing:自定义值路由到特定分片
  2. https://www.elastic.co/guide/en/elasticsearch/reference/current/paginate-search-results.html
     - 默认返回 top 10;from 定义跳过条数(默认 0),size 定义返回条数上限
     - 深分页:"每个分片必须把请求的 hits 以及前面所有页的 hits 都装进内存" ⇒ 显著增加
       内存与 CPU,可能导致节点故障
     - 默认**不能**用 from/size 翻过 10000 条,这是 index.max_result_window 的保护
     - search_after:用上一页**最后一条的 sort values** 取下一页;query 与 sort 必须不变;
       用 PIT 时 from 必须是 **0(默认)或 -1**;PIT 的 keep_alive 可随每次搜索续期
     - 排序需要 **tiebreaker**(官方示例用 _id 的一份开了 doc_values 的拷贝)
     - "不再推荐 scroll 做深分页" ⇒ 用 search_after + PIT
"""
import math
import sys

MAX_RESULT_WINDOW = 10000          # paginate-search-results:默认保护上限
DEFAULT_SIZE = 10
DEFAULT_MAX_CONCURRENT_SHARD_REQUESTS = 5

_OK = [0]
_FAIL = [0]


def check(cond, msg):
    if cond:
        _OK[0] += 1
        print("  [ok]   " + msg)
    else:
        _FAIL[0] += 1
        print("  [FAIL] " + msg)


def summary():
    print("\n" + "-" * 70)
    print("断言 %d 通过 / %d 失败" % (_OK[0], _FAIL[0]))
    print("-" * 70)
    return 0 if _FAIL[0] == 0 else 1


class Shard(object):
    def __init__(self, sid, hits):
        """hits: [(doc_id, sort_key, score_tf)] 该分片本地按 sort_key 升序"""
        self.sid = sid
        self.hits = sorted(hits, key=lambda h: (h[1], h[0]))
        self.n_docs = len(hits)

    def query_phase(self, frm, size):
        """每个分片本地取 top (from+size) 条,只回 doc id + sort values(不回 _source)"""
        return [{"shard": self.sid, "doc": h[0], "sort": [h[1], h[0]], "tf": h[2]}
                for h in self.hits[:frm + size]]


def coordinate(shards, frm=0, size=DEFAULT_SIZE, allow_partial=True, failed=()):
    """协调节点:scatter(query) → 归并 → gather(fetch)"""
    if frm + size > MAX_RESULT_WINDOW:
        raise ValueError("Result window is too large, from + size must be less than "
                         "or equal to: [%d] but was [%d]" % (MAX_RESULT_WINDOW, frm + size))
    candidates, ok_shards, bad = [], [], []
    for s in shards:
        if s.sid in failed:
            bad.append(s.sid)
            continue
        ok_shards.append(s.sid)
        candidates.extend(s.query_phase(frm, size))
    if bad and not allow_partial:
        raise RuntimeError("Search rejected: shard failures on %s "
                           "(allow_partial_search_results=false)" % bad)
    candidates.sort(key=lambda c: (c["sort"][0], c["sort"][1]))
    top = candidates[frm:frm + size]
    fetched_from = sorted({c["shard"] for c in top})
    return top, len(candidates), fetched_from, bad


def idf(n, df):
    return math.log(1.0 + (n - df + 0.5) / (df + 0.5))


# ---------------------------------------------------------------- 主流程
def main():
    print("=" * 70)
    print("Demo 1 · scatter-gather:query 阶段每片回 (from+size) 条,fetch 只去命中的片")
    print("=" * 70)
    shards = [
        Shard(0, [("a%d" % i, i, 1) for i in range(1, 12001)]),
        Shard(1, [("b%d" % i, 10 ** 6 - i, 1) for i in range(1, 12001)]),
        Shard(2, [("c%d" % i, 5 * 10 ** 5 + i, 1) for i in range(1, 12001)]),
    ]
    top, n_cand, fetched, _ = coordinate(shards, frm=0, size=5)
    print("   候选数=%d(3 片 × (0+5))  fetch 触及分片=%s" % (n_cand, fetched))
    check(n_cand == 15, "query 阶段候选数 = 分片数 × (from+size) = 3×5")
    check(len(top) == 5, "协调节点归并后只留全局 top 5")
    top3, _, fetched3, _ = coordinate(shards, frm=0, size=3)
    print("   top3 全部来自 %s ⇒ fetch 只触及 %s" % ({c['shard'] for c in top3}, fetched3))
    check(fetched3 == [0], "fetch 阶段只去**贡献了命中**的分片取 _source(不是全部 3 片)")

    print("\n" + "=" * 70)
    print("Demo 2 · 深分页:from 越大,每个分片要装进内存的条数线性增长")
    print("=" * 70)
    for frm, size in [(0, 10), (100, 10), (1000, 10), (9900, 100)]:
        _, n_cand, _, _ = coordinate(shards, frm=frm, size=size)
        print("   from=%-5d size=%-4d ⇒ 候选 %d 条(3 片 × %d)" % (frm, size, n_cand, frm + size))
    _, deep, _, _ = coordinate(shards, frm=9900, size=100)
    check(deep == 30000, "from=9900 size=100 ⇒ 3 片共装载 30000 条候选")
    try:
        coordinate(shards, frm=10000, size=1)
        check(False, "from+size > 10000 应被拒绝")
    except ValueError as e:
        check("10000" in str(e), "from+size=10001 被 max_result_window 拒绝(默认 10000)")

    print("\n" + "=" * 70)
    print("Demo 3 · search_after + PIT:游标式翻页不累积历史页")
    print("=" * 70)
    page1, _, _, _ = coordinate(shards, frm=0, size=3)
    last = page1[-1]["sort"]
    print("   第 1 页最后一条 sort=%s" % last)
    allhits = sorted([h for s in shards for h in s.hits], key=lambda h: (h[1], h[0]))

    def search_after(cursor, size=3):
        tail = [h for h in allhits if (h[1], h[0]) > (cursor[0], cursor[1])]
        return tail[:size]

    page2 = search_after(last, 3)
    print("   第 2 页: %s" % [h[0] for h in page2])
    check([h[0] for h in page2] == [h[0] for h in allhits[3:6]],
          "search_after 用上一页末条的 sort 做游标,取到真正的下 3 条")
    check(all((h[1], h[0]) > (last[0], last[1]) for h in page2),
          "游标是严格大于 ⇒ 不会重复上一页")

    def pit_search(frm):
        if frm not in (0, -1):
            raise ValueError("`from` parameter must be 0 or -1 when using a PIT")
        return True

    try:
        pit_search(10)
        check(False, "PIT 下 from=10 应被拒绝")
    except ValueError as e:
        check("0 or -1" in str(e), "用 PIT 时 from 必须 0(默认)或 -1")
    check(len(page1[-1]["sort"]) == 2,
          "sort 必须带唯一 tiebreaker:sort 值是 [排序键, _id] 两元组(否则同值文档会漏/重)")

    print("\n" + "=" * 70)
    print("Demo 4 · search_type:本地 IDF vs 全局 IDF,排序会翻转")
    print("=" * 70)
    # 两个分片各 5 篇;词 t 在 shard0 的 df=1,在 shard1 的 df=5,全局 df=6
    n_local, df0, df1 = 5, 1, 5
    n_global, df_global = 10, 6
    a_local = idf(n_local, df0) * 1      # shard0 的 docA,tf=1
    b_local = idf(n_local, df1) * 3      # shard1 的 docB,tf=3
    a_dfs = idf(n_global, df_global) * 1
    b_dfs = idf(n_global, df_global) * 3
    print("   query_then_fetch : docA=%.4f  docB=%.4f ⇒ %s 在前"
          % (a_local, b_local, "docA" if a_local > b_local else "docB"))
    print("   dfs_query_then_fetch: docA=%.4f  docB=%.4f ⇒ %s 在前"
          % (a_dfs, b_dfs, "docA" if a_dfs > b_dfs else "docB"))
    check(a_local > b_local, "本地频率下 docA 靠前(它在 df 很小的分片里)")
    check(b_dfs > a_dfs, "全局频率下 docB 靠前 ⇒ 两种 search_type 的排序不同")

    print("\n" + "=" * 70)
    print("Demo 5 · allow_partial_search_results 与分片失败")
    print("=" * 70)
    top_p, _, _, bad = coordinate(shards, size=5, allow_partial=True, failed=(1,))
    check(bad == [1] and len(top_p) == 5, "true ⇒ 分片 1 失败仍返回部分结果")
    try:
        coordinate(shards, size=5, allow_partial=False, failed=(1,))
        check(False, "false 时应报错")
    except RuntimeError as e:
        check("shard failures" in str(e), "false ⇒ 直接报错,不返回部分结果")
    print("   max_concurrent_shard_requests 默认 %d;batched_reduce_size 控制协调节点分批归并"
          % DEFAULT_MAX_CONCURRENT_SHARD_REQUESTS)
    check(DEFAULT_MAX_CONCURRENT_SHARD_REQUESTS == 5, "max_concurrent_shard_requests 默认 5")
    return summary()


if __name__ == "__main__":
    sys.exit(main())
