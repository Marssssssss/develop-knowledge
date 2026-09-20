// pprof 调用图过滤与裁剪复刻：focus/ignore 优先级、ShowFrom 栈截断、cum 口径阈值裁剪。
//
// 口径（google/pprof 源码与官方文档，本轮实读）：
//   - profile/proto：Sample.location_id **叶在下标 0**，根在末位
//   - FilterSamplesByName：只保留「至少一个帧命中 focus 且没有任何帧命中 ignore」的样本；
//     focusedAndNotIgnored 一旦遇到 ignored 就立刻返回 false ⇒ **ignore 优先于 focus**
//   - hide/show 是 **行级** 裁剪（内联帧），把 Location 摘空后该帧从栈上消失；
//     栈上所有帧都被摘空 ⇒ 整条样本丢弃
//   - ShowFrom：丢弃比「最靠根的那个匹配帧」更浅的帧；无任何匹配 ⇒ 丢弃样本
//   - internal/graph.getNodesAboveCumCutoff：abs(cum) < cutoff 才丢弃 ⇒ **等于临界值保留**，
//     且比较用的是 **cum** 不是 flat
//   - TrimLowFrequencyEdges：abs(weight) < cutoff 的边被删 ⇒ 调用图上补成虚线边
package main

import (
	"fmt"
	"regexp"
	"sort"
)

// Loc 对应 profile.Location：一个地址可含多行（被内联的函数）。
type Loc struct {
	ID    uint64
	Lines []string // 行 = 函数名
	Obj   string   // 对应 Mapping.File
}

func (l *Loc) names() []string { return l.Lines }

func (l *Loc) matchesName(rx *regexp.Regexp) bool {
	for _, n := range l.Lines {
		if rx.MatchString(n) {
			return true
		}
	}
	return l.Obj != "" && rx.MatchString(l.Obj)
}

func (l *Loc) unmatchedLines(rx *regexp.Regexp) []string {
	if l.Obj != "" && rx.MatchString(l.Obj) {
		return nil
	}
	var out []string
	for _, n := range l.Lines {
		if !rx.MatchString(n) {
			out = append(out, n)
		}
	}
	return out
}

// Sample 对应 profile.Sample：栈叶在前。
type Sample struct {
	Locs   []*Loc
	Value  int64
	Labels map[string][]string
}

type Profile struct{ Samples []*Sample }

func (p *Profile) total() int64 {
	var t int64
	for _, s := range p.Samples {
		t += s.Value
	}
	return t
}

func (p *Profile) locations() []*Loc {
	var out []*Loc
	seen := map[uint64]bool{}
	for _, s := range p.Samples {
		for _, l := range s.Locs {
			if !seen[l.ID] {
				seen[l.ID] = true
				out = append(out, l)
			}
		}
	}
	return out
}

// focusedAndNotIgnored 与 filter.go 一致：ignore 优先，且必须至少一个 focus 命中。
func focusedAndNotIgnored(locs []*Loc, m map[uint64]bool) bool {
	var f bool
	for _, l := range locs {
		if v, present := m[l.ID]; present {
			if v {
				f = true
			} else {
				return false
			}
		}
	}
	return f
}

// FilterSamplesByName 是 profile/filter.go 同名函数的简化复刻（保留执行顺序）。
func (p *Profile) FilterSamplesByName(focus, ignore, hide *regexp.Regexp) (fm, im, hm bool) {
	if focus == nil && ignore == nil && hide == nil {
		return true, false, false
	}
	focusOrIgnore := map[uint64]bool{}
	hidden := map[uint64]bool{}
	for _, l := range p.locations() {
		if ignore != nil && l.matchesName(ignore) {
			im = true
			focusOrIgnore[l.ID] = false
		} else if focus == nil || l.matchesName(focus) {
			fm = true
			focusOrIgnore[l.ID] = true
		}
		if hide != nil {
			l.Lines = l.unmatchedLines(hide)
			if len(l.Lines) == 0 {
				hidden[l.ID] = true
			} else {
				hm = true
			}
		}
	}
	kept := make([]*Sample, 0, len(p.Samples))
	for _, s := range p.Samples {
		if !focusedAndNotIgnored(s.Locs, focusOrIgnore) {
			continue
		}
		if len(hidden) > 0 {
			var locs []*Loc
			for _, l := range s.Locs {
				if !hidden[l.ID] {
					locs = append(locs, l)
				}
			}
			if len(locs) == 0 {
				continue
			}
			s.Locs = locs
		}
		kept = append(kept, s)
	}
	p.Samples = kept
	return fm, im, hm
}

// ShowFrom 与 filter.go 一致：从根侧（下标末位）往叶侧找第一个匹配。
func (p *Profile) ShowFrom(rx *regexp.Regexp) bool {
	if rx == nil {
		return false
	}
	matchedLocs := map[uint64]bool{}
	matched := false
	for _, l := range p.locations() {
		if l.matchesName(rx) {
			matchedLocs[l.ID] = true
			matched = true
		}
	}
	kept := make([]*Sample, 0, len(p.Samples))
	for _, s := range p.Samples {
		for i := len(s.Locs) - 1; i >= 0; i-- {
			if matchedLocs[s.Locs[i].ID] {
				s.Locs = s.Locs[:i+1]
				kept = append(kept, s)
				break
			}
		}
	}
	p.Samples = kept
	return matched
}

// Cum 统计：帧在栈上出现过就计入（graph.go 的裁剪口径）。
func (p *Profile) Cum() map[string]int64 {
	cum := map[string]int64{}
	for _, s := range p.Samples {
		seen := map[string]bool{}
		for _, l := range s.Locs {
			for _, n := range l.names() {
				if !seen[n] {
					seen[n] = true
					cum[n] += s.Value
				}
			}
		}
	}
	return cum
}

type node struct {
	Name string
	Cum  int64
}

// DiscardLowFrequencyNodes 复刻 getNodesAboveCumCutoff：abs(cum) < cutoff 才丢弃。
func DiscardLowFrequencyNodes(cum map[string]int64, total int64, fraction float64) []node {
	cutoff := float64(total) * fraction
	var kept []node
	for n, v := range cum {
		if v < 0 {
			v = -v
		}
		if float64(v) >= cutoff-1e-9 {
			kept = append(kept, node{n, cum[n]})
		}
	}
	sort.Slice(kept, func(i, j int) bool {
		if kept[i].Cum != kept[j].Cum {
			return kept[i].Cum > kept[j].Cum
		}
		return kept[i].Name < kept[j].Name
	})
	return kept
}

func loc(id uint64, names ...string) *Loc { return &Loc{ID: id, Lines: names} }

func main() {
	alloc, work, main_, gc := loc(1, "alloc"), loc(2, "work"), loc(3, "main"), loc(4, "gc")
	p := &Profile{Samples: []*Sample{
		{Locs: []*Loc{alloc, work, main_}, Value: 60},
		{Locs: []*Loc{work, main_}, Value: 30},
		{Locs: []*Loc{gc, main_}, Value: 10},
	}}
	fmt.Printf("total=%d cum=%v\n", p.total(), p.Cum())

	q := &Profile{Samples: []*Sample{
		{Locs: []*Loc{loc(11, "alloc"), loc(12, "work"), loc(13, "main")}, Value: 60},
		{Locs: []*Loc{loc(12, "work"), loc(13, "main")}, Value: 30},
		{Locs: []*Loc{loc(14, "gc"), loc(13, "main")}, Value: 10},
	}}
	fm, im, _ := q.FilterSamplesByName(regexp.MustCompile("work"), regexp.MustCompile("alloc"), nil)
	fmt.Printf("focus=work ignore=alloc -> fm=%v im=%v left=%d\n", fm, im, len(q.Samples))

	r := &Profile{Samples: []*Sample{
		{Locs: []*Loc{loc(21, "B"), loc(22, "C"), loc(23, "B"), loc(24, "A")}, Value: 10},
	}}
	r.ShowFrom(regexp.MustCompile("C"))
	fmt.Println("ShowFrom(C) leaf-first:", r.Samples[0].Locs[0].Lines, len(r.Samples[0].Locs))

	cum := map[string]int64{"main": 100, "work": 90, "alloc": 60, "gc": 10}
	for _, f := range []float64{0.10, 0.101} {
		fmt.Printf("nodefraction=%v kept=%v\n", f, DiscardLowFrequencyNodes(cum, 100, f))
	}
}
