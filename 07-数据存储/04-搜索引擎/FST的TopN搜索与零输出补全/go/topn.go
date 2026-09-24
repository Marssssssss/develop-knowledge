// Util.TopNSearcher：如何在不遍历全部候选的前提下取 top-N。
//
// 转写自 apache/lucene@main
//   lucene/core/src/java/org/apache/lucene/util/fst/Util.java（TopNSearcher 全篇）
//
// 这是 NRTSuggester 里 `// search admissibility is not guaranteed` 的出处：
// 队列深度 maxQueueDepth 满了以后最差的会被挤掉，结果可能不是全局 top-N；
// 结尾用 `rejectCount + topN <= maxQueueDepth` 判断"是否完整"。
package main

import (
	"errors"
	"sort"
)

// ArcCursor 弧游标：指向 (node, idx) 这一条弧。
type ArcCursor struct {
	Node *Node
	Idx  int
}

// Label / Output / Target / IsLast 弧的四个属性。
func (c *ArcCursor) Label() int  { return c.Node.Arcs[c.Idx].Label }
func (c *ArcCursor) Output() int { return c.Node.Arcs[c.Idx].Output }
func (c *ArcCursor) Target() *Node { return c.Node.Arcs[c.Idx].Target }
func (c *ArcCursor) IsLast() bool { return c.Idx == len(c.Node.Arcs)-1 }

// CopyFrom 照抄 Arc.copyFrom。
func (c *ArcCursor) CopyFrom(o *ArcCursor) { c.Node = o.Node; c.Idx = o.Idx }

// FSTPath 搜索中的一条路径。
type FSTPath struct {
	Output int
	Arc    *ArcCursor
	Input  []int
}

// Result 一条搜索结果。
type Result struct {
	Input  []int
	Output int
}

// TopResults 搜索结果集合 + 是否完整。
type TopResults struct {
	IsComplete bool
	Results    []Result
}

// CompareOutputs 默认 comparator：整数升序（升序即"top"，配合权重编码使用）。
func CompareOutputs(a, b int) int {
	if a < b {
		return -1
	}
	if a > b {
		return 1
	}
	return 0
}

// TieBreakByInputComparator 先比 output，同分比 input 字典序。
func TieBreakByInputComparator(p1, p2 FSTPath) int {
	if c := CompareOutputs(p1.Output, p2.Output); c != 0 {
		return c
	}
	return lexCompare(p1.Input, p2.Input)
}

func lexCompare(a, b []int) int {
	n := len(a)
	if len(b) < n {
		n = len(b)
	}
	for i := 0; i < n; i++ {
		if a[i] != b[i] {
			if a[i] < b[i] {
				return -1
			}
			return 1
		}
	}
	if len(a) < len(b) {
		return -1
	}
	if len(a) > len(b) {
		return 1
	}
	return 0
}

// TopNSearcher Util.TopNSearcher 的 Go 转写。
type TopNSearcher struct {
	Fst            *FST
	TopN           int
	MaxQueueDepth  int
	Queue          []*FSTPath
	ScratchArc     *ArcCursor
	RejectCount    int
	PartialRejects int
}

// NewTopNSearcher 构造一个搜索器。
func NewTopNSearcher(fst *FST, topN, maxQueueDepth int) *TopNSearcher {
	return &TopNSearcher{Fst: fst, TopN: topN, MaxQueueDepth: maxQueueDepth}
}

// AcceptPartialPath 可覆写的钩子：某条路径在走完之前就被否掉。
func (s *TopNSearcher) AcceptPartialPath(p *FSTPath) bool { return true }

// AcceptResult 可覆写的钩子：某条完整路径被否掉（会计入 rejectCount）。
func (s *TopNSearcher) AcceptResult(p *FSTPath) bool { return true }

// AddIfCompetitive 队列满时才与最差的比较；赢了才能入队。
func (s *TopNSearcher) AddIfCompetitive(p *FSTPath) bool {
	if s.Queue == nil {
		panic("queue must not be nil")
	}
	output := p.Output + p.Arc.Output()
	if len(s.Queue) == s.MaxQueueDepth {
		bottom := s.Queue[len(s.Queue)-1]
		if TieBreakByInputComparator(*p, *bottom) > 0 {
			return false // Doesn't compete
		}
		if TieBreakByInputComparator(*p, *bottom) == 0 {
			a := append(append([]int{}, p.Input...), p.Arc.Label())
			if lexCompare(bottom.Input, a) < 0 {
				return false
			}
		}
	}
	np := &FSTPath{output, &ArcCursor{p.Arc.Node, p.Arc.Idx},
		append(append([]int{}, p.Input...), p.Arc.Label())}
	if !s.AcceptPartialPath(np) {
		s.PartialRejects++
		return false
	}
	s.insertSorted(np)
	if len(s.Queue) == s.MaxQueueDepth+1 {
		s.Queue = s.Queue[:len(s.Queue)-1]
	}
	return true
}

func (s *TopNSearcher) insertSorted(p *FSTPath) {
	i := sort.Search(len(s.Queue), func(i int) bool {
		return TieBreakByInputComparator(*s.Queue[i], *p) >= 0
	})
	s.Queue = append(s.Queue, nil)
	copy(s.Queue[i+1:], s.Queue[i:])
	s.Queue[i] = p
}

// AddStartPaths 把 node 的全部出弧入队；END_LABEL 那条只在 allowEmptyString 时入队。
func (s *TopNSearcher) AddStartPaths(node *Node, startOutput int, input []int,
	allowEmptyString bool) {
	if startOutput == NoOutput {
		startOutput = NoOutput // De-dup NO_OUTPUT since it must be a singleton
	}
	p := &FSTPath{startOutput, &ArcCursor{node, 0}, append([]int{}, input...)}
	for {
		if allowEmptyString || p.Arc.Label() != EndLabel {
			s.AddIfCompetitive(p)
		}
		if p.Arc.IsLast() {
			break
		}
		p.Arc.Idx++
	}
}

// Search 执行 top-N 搜索。
func (s *TopNSearcher) Search() (*TopResults, error) {
	results := []Result{}
	for len(results) < s.TopN {
		if s.Queue == nil || len(s.Queue) == 0 {
			break
		}
		p := s.Queue[0]
		s.Queue = s.Queue[1:]
		if !s.AcceptPartialPath(p) {
			s.PartialRejects++
			continue
		}
		if p.Arc.Label() == EndLabel {
			// Empty string!
			p.Input = p.Input[:len(p.Input)-1]
			results = append(results, Result{p.Input, p.Output})
			continue
		}
		if len(results) == s.TopN-1 && s.MaxQueueDepth == s.TopN {
			// Last path -- don't bother w/ queue anymore
			s.Queue = nil
		}
		// 零输出补全
		for {
			p.Arc.Node = p.Arc.Target()
			p.Arc.Idx = 0
			foundZero := false
			arcCopyPending := false
			for {
				if CompareOutputs(NoOutput, p.Arc.Output()) == 0 {
					if s.Queue == nil {
						foundZero = true
						break
					}
					if !foundZero {
						arcCopyPending = true
						foundZero = true
					} else {
						s.AddIfCompetitive(p)
					}
				} else if s.Queue != nil {
					s.AddIfCompetitive(p)
				}
				if p.Arc.IsLast() {
					break
				}
				if arcCopyPending {
					s.ScratchArc = &ArcCursor{p.Arc.Node, p.Arc.Idx}
					arcCopyPending = false
				}
				p.Arc.Idx++
			}
			if !foundZero {
				return nil, errors.New("每个节点必须至少有一条 NO_OUTPUT 弧（源码 assert foundZero）")
			}
			if s.Queue != nil && !arcCopyPending && s.ScratchArc != nil {
				p.Arc.CopyFrom(s.ScratchArc)
			}
			if p.Arc.Label() == EndLabel {
				p.Output += p.Arc.Output()
				if s.AcceptResult(p) {
					results = append(results, Result{p.Input, p.Output})
				} else {
					s.RejectCount++
				}
				break
			}
			p.Input = append(p.Input, p.Arc.Label())
			p.Output += p.Arc.Output()
			if !s.AcceptPartialPath(p) {
				s.PartialRejects++
				break
			}
		}
	}
	return &TopResults{s.RejectCount + s.TopN <= s.MaxQueueDepth, results}, nil
}
