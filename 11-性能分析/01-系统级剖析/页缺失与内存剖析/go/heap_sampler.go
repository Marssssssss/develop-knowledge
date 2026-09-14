// heap_sampler.go — Massif 式堆采样剖析模型(Go 版,教学用)
//
// 复刻 valgrind Massif 的三个关键机制(valgrind.org/docs/manual/ms-manual.html):
//   1) 采样降频:起初每次 malloc/free 都拍快照,随运行时间降频;达到 --max-snapshots
//      (默认 100)时【删掉一半】旧快照
//   2) 快照分三类:普通(:)、详细(@,默认每第 10 个)、峰值(#,最多一个);
//      峰值【只在释放之后】才可能被记录,且默认只保证真实峰值 1% 以内(--peak-inaccuracy)
//   3) 详细快照给出【分配树】:每行是一个代码位置(不是函数)贡献的字节数与占比,
//      并满足不变量:某条目的大小 == 其子条目大小之和
//
// 峰值的分配树刻意采用官方文档里的示例数据(20000B / 三个代码位置),便于逐字节核对。
//
// 运行: go run .
package main

import (
	"fmt"
	"math/rand"
	"sort"
)

const (
	maxSnapshots   = 100   // --max-snapshots [默认 100]
	detailedFreq   = 10    // --detailed-freq [默认 10]
	peakInaccuracy = 1.0   // --peak-inaccuracy [默认 1.0 %]
)

type frame struct {
	label string // 例如 "main (example.c:20)"
	size  int
	kids  []*frame
}

func (f *frame) find(label string) *frame {
	for _, k := range f.kids {
		if k.label == label {
			return k
		}
	}
	return nil
}

// insert 把一条栈回溯(第 0 个是分配点,之后是它的调用者)累加进分配树
func (f *frame) insert(path []string, size int) {
	cur := f
	for _, label := range path {
		next := cur.find(label)
		if next == nil {
			next = &frame{label: label}
			cur.kids = append(cur.kids, next)
		}
		next.size += size
		cur = next
	}
}

// checkInvariant 校验"条目大小 == 子条目之和"(文档里强调的不变量)
func (f *frame) checkInvariant() []string {
	var bad []string
	var walk func(n *frame)
	walk = func(n *frame) {
		sum := 0
		for _, k := range n.kids {
			sum += k.size
		}
		if len(n.kids) > 0 && sum != n.size {
			bad = append(bad, fmt.Sprintf("%s: %d != Σ子节点 %d", n.label, n.size, sum))
		}
		for _, k := range n.kids {
			walk(k)
		}
	}
	walk(f)
	return bad
}

func (f *frame) print(prefix string, total int) {
	sort.Slice(f.kids, func(i, j int) bool { return f.kids[i].size > f.kids[j].size })
	for _, k := range f.kids {
		pct := 100.0 * float64(k.size) / float64(total)
		fmt.Printf("  %s%5.2f%% (%6dB) %s\n", prefix, pct, k.size, k.label)
		k.print(prefix+"  ", total)
	}
}

// ---------------- 采样状态机 ----------------
type snapshot struct {
	idx     int
	kind    string // normal / detailed / peak
	timeB   int64  // --time-unit=B:横轴用"堆操作字节数"
	totalB  int
	tree    *frame // 只有 detailed/peak 才记录分配树
	liveMap map[int]alloc
}

type alloc struct {
	site string
	path []string
	size int
}

type sampler struct {
	snaps      []snapshot
	nextDetail int
	interval   int64 // 当前采样间隔(以"分配次数"计),每次采样后翻倍 = 降频
	sinceSnap  int64
	cumBytes   int64 // 累计堆操作字节数,--time-unit=B 的横轴
	seq        int
	peak       int
	peakSnap   *snapshot
}

func newSampler() *sampler {
	return &sampler{nextDetail: detailedFreq, interval: 1}
}

// maybeSnapshot 返回 nil 表示本次不拍(尚未跨过一个采样间隔)
func (s *sampler) maybeSnapshot(live map[int]alloc, justFreed bool, deltaBytes int64) *snapshot {
	s.cumBytes += deltaBytes
	s.sinceSnap++
	if s.sinceSnap < s.interval {
		return nil
	}
	s.sinceSnap = 0
	if s.interval < 64 {
		s.interval *= 2 // 随运行时间降频
	}
	s.seq++
	kind := "normal"
	if s.seq%s.nextDetail == 0 {
		kind = "detailed"
	}
	total := 0
	tree := &frame{label: "(heap allocation functions) malloc/new/new[], --alloc-fns, etc."}
	ids := make([]int, 0, len(live))
	for id := range live {
		ids = append(ids, id)
	}
	sort.Ints(ids)
	for _, id := range ids {
		a := live[id]
		total += a.size
		tree.insert(a.path, a.size)
	}
	snap := snapshot{idx: s.seq, kind: kind, timeB: s.cumBytes, totalB: total,
		tree: tree, liveMap: copyLive(live)}

	// 峰值只在"刚发生释放"后检查,且要求超出旧峰值超过 peakInaccuracy%
	if justFreed && float64(total) > float64(s.peak)*(1+peakInaccuracy/100) {
		s.peak = total
		cp := snap
		cp.kind = "peak"
		s.peakSnap = &cp
	}

	s.snaps = append(s.snaps, snap)
	// 达到上限:删掉一半旧快照(文档:最终快照数落在 N/2 ~ N 之间)
	if len(s.snaps) > maxSnapshots {
		keep := make([]snapshot, 0, maxSnapshots/2+1)
		keep = append(keep, s.snaps[len(s.snaps)/2:]...)
		s.snaps = keep
		s.nextDetail = detailedFreq // 重新计数
	}
	return &snap
}

func copyLive(live map[int]alloc) map[int]alloc {
	out := make(map[int]alloc, len(live))
	for k, v := range live {
		out[k] = v
	}
	return out
}

// ---------------- 主流程 ----------------
func main() {
	fmt.Println("=== Massif 采样机制:降频 + 删半 + 三类快照 ===")
	s := newSampler()
	live := map[int]alloc{}
	rng := rand.New(rand.NewSource(7))
	sites := []struct {
		path  []string
		size  int
		share int
	}{
		{[]string{"main (example.c:20)"}, 10000, 25},
		{[]string{"g (example.c:5)", "f (example.c:11)", "main (example.c:23)"}, 4000, 20},
		{[]string{"g (example.c:5)", "main (example.c:25)"}, 4000, 20},
		{[]string{"f (example.c:10)", "main (example.c:23)"}, 2000, 15},
		{[]string{"f (example.c:10)", "main (example.c:23)"}, 800, 20},
	}
	id := 0
	for i := 0; i < 6000; i++ {
		// 分配:按 share 权重随机选一个代码位置
		total := 0
		for _, s := range sites {
			total += s.share
		}
		roll, acc := rng.Intn(total), 0
		chosen := sites[0]
		for _, s := range sites {
			acc += s.share
			if roll < acc {
				chosen = s
				break
			}
		}
		id++
		live[id] = alloc{site: chosen.path[0], path: chosen.path, size: chosen.size}
		s.maybeSnapshot(live, false, int64(chosen.size))

		// 释放:每 3 次分配释放一个最老的
		if i%3 == 0 && len(live) > 3 {
			oldest := id
			for k := range live {
				if k < oldest {
					oldest = k
				}
			}
			freed := live[oldest]
			delete(live, oldest)
			s.maybeSnapshot(live, true, int64(-freed.size))
		}
	}
	fmt.Printf("  模拟 6000 轮分配/释放后:保留快照 %d 个(上限 %d,超出即删一半)\n",
		len(s.snaps), maxSnapshots)
	kinds := map[string]int{}
	for _, sn := range s.snaps {
		kinds[sn.kind]++
	}
	fmt.Printf("  快照类型分布:normal=%d detailed=%d peak=%d(peak 至多 1 个)\n",
		kinds["normal"], kinds["detailed"], kinds["peak"])

	fmt.Println("\n=== 内存曲线(横轴 = 堆操作字节数,--time-unit=B)===")
	maxB := 0
	for _, sn := range s.snaps {
		if sn.totalB > maxB {
			maxB = sn.totalB
		}
	}
	step := 1
	if len(s.snaps) > 40 {
		step = len(s.snaps) / 40
	}
	var buf []byte
	for i := 0; i < len(s.snaps); i += step {
		sn := s.snaps[i]
		w := 0
		if maxB > 0 {
			w = 60 * sn.totalB / maxB
		}
		buf = buf[:0]
		marker := map[string]string{"detailed": "@", "peak": "#"}[sn.kind]
		if marker == "" {
			marker = " "
		}
		for j := 0; j < w; j++ {
			buf = append(buf, '#')
		}
		fmt.Printf("  %4dB %s|%s %d B\n", sn.timeB, marker, string(buf), sn.totalB)
	}
	fmt.Println("  最左侧字符:'@' = 详细快照(记录分配树),'#' = 峰值快照,其余为普通快照")

	fmt.Println("\n=== 峰值快照的分配树(逐字节核对官方示例的 20000 B / 3 个位置)===")
	demo := &frame{label: "(heap allocation functions) malloc/new/new[], --alloc-fns, etc."}
	totalDemo := 20000
	// 官方文档 ms_print 示例的峰值快照 14
	demo.insert([]string{"main (example.c:20)"}, 10000)
	demo.insert([]string{"g (example.c:5)", "f (example.c:11)", "main (example.c:23)"}, 4000)
	demo.insert([]string{"g (example.c:5)", "main (example.c:25)"}, 4000)
	demo.insert([]string{"f (example.c:10)", "main (example.c:23)"}, 2000)
	fmt.Printf("  %5.2f%% (%6dB) %s\n", 100.0, totalDemo, demo.label)
	demo.print("  ", totalDemo)
	if bad := demo.checkInvariant(); len(bad) == 0 {
		fmt.Println("  不变量校验:每个条目大小 == 其子条目之和  -> 通过")
	} else {
		fmt.Printf("  不变量校验失败(畸形栈回溯,文档提到罕见):%v\n", bad)
	}

	fmt.Println("\n=== 局限性(实现前必须知道)===")
	limitations := []string{
		"默认只测堆(malloc/calloc/realloc/memalign/new/new[]),不直接测 mmap/mremap/brk",
		"也不测代码段/数据段/BSS => 报告值可能显著小于 top(要用 --pages-as-heap 才看全)",
		"峰值只在【释放之后】记录:从不释放则无峰值;涨到新高却不再释放 => 峰值偏低",
		"默认只保证真实峰值 1% 以内(--peak-inaccuracy),设 0 更准但明显更慢",
		"--stacks=yes 会大幅拖慢程序(栈的分析默认关闭)",
		"fork 的子进程数据会与父进程混在一个输出文件里,除非 --massif-out-file 用 %p",
	}
	for _, l := range limitations {
		fmt.Println("  ·", l)
	}
	if s.peakSnap != nil {
		fmt.Printf("\n  本次模拟的峰值快照 = #%d,记录到 %d B\n", s.peakSnap.idx, s.peak)
	} else {
		fmt.Println("\n  本次模拟未记录到峰值快照(峰值只在【释放之后】才可能被记录)")
	}
	fmt.Println("  实践:先看曲线形状(是否符合预期、有没有异常尖峰),再下钻峰值快照的分配树。")
}
