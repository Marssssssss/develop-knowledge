// FST（前缀 trie）与 top-N 搜索器。
//
// 与 suggester.go 同属 package main；真实 FST 是带后缀共享的最小化 DAG，
// 这里用 trie 代替，因为被断言的性质只依赖「路径输出可累加」。
package main

import (
	"sort"
)

// ------------------------------------------------------------------ FST（trie）

// FstNode trie 节点。终点挂**一串** output，因为同一个 surface 可能有多条路径。
type FstNode struct {
	Arcs     map[byte]*FstNode
	Outputs  []FstOutput
	Terminal bool
}

// FstOutput 一对 (output1, output2)：output1 是编码后的权重，output2 是 payload。
type FstOutput struct {
	Out1 int64
	Out2 []byte
}

func NewFstNode() *FstNode {
	return &FstNode{Arcs: map[byte]*FstNode{}}
}

// SuggesterFST 把 (surface, weight, docID) 建成前缀 trie。
type SuggesterFST struct {
	Root *FstNode
	PP   PayloadProcessor
}

func NewSuggesterFST() *SuggesterFST {
	return &SuggesterFST{Root: NewFstNode(), PP: PayloadProcessor{Sep: defaultPayloadSep}}
}

func (f *SuggesterFST) Add(surface string, weight, docID int) {
	node := f.Root
	for i := 0; i < len(surface); i++ {
		ch := surface[i]
		if node.Arcs[ch] == nil {
			node.Arcs[ch] = NewFstNode()
		}
		node = node.Arcs[ch]
	}
	node.Terminal = true
	node.Outputs = append(node.Outputs,
		FstOutput{Encode(weight), f.PP.Make([]byte(surface), docID)})
}

// IntersectPrefixPaths FSTUtil.intersectPrefixPaths 的前缀特化。
func (f *SuggesterFST) IntersectPrefixPaths(prefix string) []*FstNode {
	node := f.Root
	for i := 0; i < len(prefix); i++ {
		node = node.Arcs[prefix[i]]
		if node == nil {
			return nil
		}
	}
	return []*FstNode{node}
}

// ------------------------------------------------------------------ TopNSearcher

// Hit 一条补全结果。
type Hit struct {
	Doc     int
	Surface string
	Weight  int64
	Score   float64
}

// TopNSearcher bounded 优先队列版 top-N 搜索。
type TopNSearcher struct {
	Fst          *SuggesterFST
	TopN         int
	QueueSize    int
	Dedup        bool
	Boost        float64
	seen         map[string]bool
	Results      []Hit
	Pruned       bool
}

func NewTopNSearcher(f *SuggesterFST, topN, queueSize int, dedup bool) *TopNSearcher {
	return &TopNSearcher{Fst: f, TopN: topN, QueueSize: queueSize, Dedup: dedup,
		Boost: 1.0, seen: map[string]bool{}}
}

func (s *TopNSearcher) acceptResult(out1 int64, out2 []byte) bool {
	surface, sepIdx := s.Fst.PP.ParseSurfaceForm(out2)
	docID, _ := ReadVInt(out2, sepIdx+1)
	key := string(surface)
	if s.Dedup {
		if s.seen[key] {
			return false
		}
		s.seen[key] = true
	}
	s.Results = append(s.Results, Hit{docID, key, Decode(out1),
		float64(Decode(out1)) * s.Boost})
	return true
}

func (s *TopNSearcher) expand(node *FstNode) {
	if node.Terminal {
		accepted := false
		for _, o := range node.Outputs {
			if s.acceptResult(o.Out1, o.Out2) {
				accepted = true
			} else {
				s.Pruned = true
			}
		}
		// skipDuplicates 的「部分路径剪枝」：整条路径的 surface 都见过了就不必再往下走
		if s.Dedup && !accepted {
			s.Pruned = true
			return
		}
	}
	labels := make([]int, 0, len(node.Arcs))
	for k := range node.Arcs {
		labels = append(labels, int(k))
	}
	sort.Ints(labels)
	for _, l := range labels {
		s.expand(node.Arcs[byte(l)])
	}
}

// Search 排序键：score 降序，同分按 surface 升序（对应 ScoringPathComparator）。
func (s *TopNSearcher) Search() []Hit {
	sort.Slice(s.Results, func(i, j int) bool {
		if s.Results[i].Score != s.Results[j].Score {
			return s.Results[i].Score > s.Results[j].Score
		}
		return s.Results[i].Surface < s.Results[j].Surface
	})
	if len(s.Results) > s.TopN {
		s.Pruned = true
		s.Results = s.Results[:s.TopN]
	}
	return s.Results
}

// ------------------------------------------------------------------ 编排

// SuggestLookup 复刻 NRTSuggester.lookup 的编排顺序。
type SuggestLookup struct {
	Fst                        *SuggesterFST
	MaxAnalyzedPathsPerOutput  int
}

// Plan 返回 (liveDocsRatio, topN, queueSize)；ratio == -1 表示直接 return。
func (l *SuggestLookup) Plan(countToCollect, numDocs, maxDocs int,
	filterEnabled bool, pathsPerOutput int) (float64, int, int) {
	ratio := CalculateLiveDocRatio(numDocs, maxDocs)
	if ratio == -1 {
		return -1, 0, 0
	}
	topN := countToCollect * pathsPerOutput
	q := GetMaxTopNQueueSize(topN, numDocs, ratio, filterEnabled,
		l.MaxAnalyzedPathsPerOutput)
	return ratio, topN, q
}

func (l *SuggestLookup) Lookup(prefix string, countToCollect, numDocs, maxDocs int,
	filterEnabled, dedup bool) []Hit {
	ratio, _, _ := l.Plan(countToCollect, numDocs, maxDocs, filterEnabled, 1)
	if ratio == -1 {
		return nil
	}
	starts := l.Fst.IntersectPrefixPaths(prefix)
	if len(starts) == 0 {
		return nil
	}
	topN := countToCollect * len(starts)
	q := GetMaxTopNQueueSize(topN, numDocs, ratio, filterEnabled,
		l.MaxAnalyzedPathsPerOutput)
	s := NewTopNSearcher(l.Fst, topN, q, dedup)
	for _, n := range starts {
		s.expand(n)
	}
	return s.Search()
}
