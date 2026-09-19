// ES 分布式检索两阶段与深分页(Go),与 python/scatter_gather.py 同构。
//
// 权威来源(实际读过):
//   1. .../search-search.html —— search_type 只支持 query_then_fetch(分片**本地**频率)
//      与 dfs_query_then_fetch(**跨分片全局**频率);allow_partial_search_results 的两种行为;
//      batched_reduce_size;max_concurrent_shard_requests 默认 5;preference 用
//      adaptive replica selection 挑副本;routing 路由到特定分片
//   2. .../paginate-search-results.html —— from/size 深分页每片都要装 (from+size) 条;
//      默认上限 index.max_result_window = 10000;search_after 用上一页末条 sort 做游标;
//      用 PIT 时 from 必须是 0 或 -1;排序需要 tiebreaker
package main

import (
	"fmt"
	"math"
	"os"
	"sort"
)

const (
	maxResultWindow = 10000
	defaultSize     = 10
	maxConcurrent   = 5
)

var okCount, failCount int

func check(cond bool, msg string) {
	if cond {
		okCount++
		fmt.Println("  [ok]   " + msg)
	} else {
		failCount++
		fmt.Println("  [FAIL] " + msg)
	}
}

type Hit struct {
	Doc  string
	Sort int
	Tf   float64
}

type Shard struct {
	ID   int
	Hits []Hit
}

func newShard(id, n int, base int, desc bool) *Shard {
	hits := make([]Hit, 0, n)
	for i := 1; i <= n; i++ {
		k := base + i
		if desc {
			k = base - i
		}
		hits = append(hits, Hit{fmt.Sprintf("s%d-%d", id, i), k, 1})
	}
	sort.Slice(hits, func(a, b int) bool {
		if hits[a].Sort == hits[b].Sort {
			return hits[a].Doc < hits[b].Doc
		}
		return hits[a].Sort < hits[b].Sort
	})
	return &Shard{id, hits}
}

// queryPhase 每片本地取 top (from+size) 条,只回 doc id + sort values
func (s *Shard) queryPhase(frm, size int) []Hit {
	n := frm + size
	if n > len(s.Hits) {
		n = len(s.Hits)
	}
	out := make([]Hit, n)
	copy(out, s.Hits[:n])
	return out
}

type cand struct {
	shard int
	h     Hit
}

func coordinate(shards []*Shard, frm, size int, allowPartial bool, failed map[int]bool) (
	[]cand, int, []int, error) {
	if frm+size > maxResultWindow {
		return nil, 0, nil, fmt.Errorf(
			"Result window is too large, from + size must be less than or equal to: [%d] but was [%d]",
			maxResultWindow, frm+size)
	}
	var cands []cand
	bad := []int{}
	for _, s := range shards {
		if failed[s.ID] {
			bad = append(bad, s.ID)
			continue
		}
		for _, h := range s.queryPhase(frm, size) {
			cands = append(cands, cand{s.ID, h})
		}
	}
	if len(bad) > 0 && !allowPartial {
		return nil, 0, nil, fmt.Errorf("Search rejected: shard failures on %v", bad)
	}
	sort.SliceStable(cands, func(a, b int) bool {
		if cands[a].h.Sort == cands[b].h.Sort {
			return cands[a].h.Doc < cands[b].h.Doc
		}
		return cands[a].h.Sort < cands[b].h.Sort
	})
	top := []cand{}
	if frm < len(cands) {
		end := frm + size
		if end > len(cands) {
			end = len(cands)
		}
		top = cands[frm:end]
	}
	seen := map[int]bool{}
	fetched := []int{}
	for _, c := range top {
		if !seen[c.shard] {
			seen[c.shard] = true
			fetched = append(fetched, c.shard)
		}
	}
	sort.Ints(fetched)
	return top, len(cands), fetched, nil
}

func idf(n, df float64) float64 {
	return math.Log(1.0 + (n-df+0.5)/(df+0.5))
}

func main() {
	shards := []*Shard{
		newShard(0, 12000, 0, false),
		newShard(1, 12000, 1000000, true),
		newShard(2, 12000, 500000, false),
	}
	fmt.Println("== Demo 1 · scatter-gather ==")
	top, nCand, fetched, _ := coordinate(shards, 0, 5, true, nil)
	fmt.Printf("   候选=%d  fetch 分片=%v\n", nCand, fetched)
	check(nCand == 15, "query 阶段候选数 = 3 片 × (0+5)")
	check(len(top) == 5, "协调节点归并后只留全局 top 5")
	_, _, f3, _ := coordinate(shards, 0, 3, true, nil)
	check(len(f3) == 1 && f3[0] == 0, "fetch 只去贡献命中的分片(top3 全在片 0)")

	fmt.Println("\n== Demo 2 · 深分页 ==")
	_, n, _, _ := coordinate(shards, 9900, 100, true, nil)
	fmt.Printf("   from=9900 size=100 ⇒ 候选 %d 条\n", n)
	check(n == 30000, "from=9900 size=100 ⇒ 3 片共装载 30000 条")
	_, _, _, err := coordinate(shards, 10000, 1, true, nil)
	check(err != nil, "from+size=10001 被 max_result_window(10000)拒绝")

	fmt.Println("\n== Demo 3 · search_after / PIT ==")
	page1, _, _, _ := coordinate(shards, 0, 3, true, nil)
	last := page1[len(page1)-1].h
	all := []Hit{}
	for _, s := range shards {
		all = append(all, s.Hits...)
	}
	sort.Slice(all, func(a, b int) bool {
		if all[a].Sort == all[b].Sort {
			return all[a].Doc < all[b].Doc
		}
		return all[a].Sort < all[b].Sort
	})
	var page2 []Hit
	for _, h := range all {
		if h.Sort > last.Sort || (h.Sort == last.Sort && h.Doc > last.Doc) {
			page2 = append(page2, h)
			if len(page2) == 3 {
				break
			}
		}
	}
	fmt.Printf("   游标=[%d,%s] ⇒ 第 2 页 %v\n", last.Sort, last.Doc,
		[]string{page2[0].Doc, page2[1].Doc, page2[2].Doc})
	check(page2[0].Doc == all[3].Doc, "search_after 取到真正的下 3 条")
	check(maxConcurrent == 5, "max_concurrent_shard_requests 默认 5")

	fmt.Println("\n== Demo 4 · search_type:本地 IDF vs 全局 IDF ==")
	aL, bL := idf(5, 1)*1, idf(5, 5)*3
	aG, bG := idf(10, 6)*1, idf(10, 6)*3
	fmt.Printf("   query_then_fetch    : A=%.4f B=%.4f\n", aL, bL)
	fmt.Printf("   dfs_query_then_fetch: A=%.4f B=%.4f\n", aG, bG)
	check(aL > bL, "本地频率下 docA 靠前")
	check(bG > aG, "全局频率下 docB 靠前 ⇒ 两种 search_type 排序翻转")

	fmt.Println("\n== Demo 5 · 部分结果 ==")
	_, _, _, errP := coordinate(shards, 0, 5, true, map[int]bool{1: true})
	check(errP == nil, "allow_partial=true ⇒ 分片失败仍返回部分结果")
	_, _, _, errF := coordinate(shards, 0, 5, false, map[int]bool{1: true})
	check(errF != nil, "allow_partial=false ⇒ 直接报错,不返回部分结果")

	fmt.Printf("\n断言 %d 通过 / %d 失败\n", okCount, failCount)
	if failCount > 0 {
		os.Exit(1)
	}
}
