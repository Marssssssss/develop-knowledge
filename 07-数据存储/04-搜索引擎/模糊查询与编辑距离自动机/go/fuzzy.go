// FuzzyQuery 参数语义与 Levenshtein 自动机的参数化描述（Go 侧等价实现）。
//
// 转写自 apache/lucene@main
//   lucene/core/src/java/org/apache/lucene/search/FuzzyQuery.java
//   lucene/core/src/java/org/apache/lucene/util/automaton/LevenshteinAutomata.java
//   .../automaton/Lev1ParametricDescription.java / Lev2ParametricDescription.java /
//   Lev1TParametricDescription.java
//
// 语言差异（已在代码中显式落地）：
//   * Java 抛 IllegalArgumentException，Go 侧用 error 返回；
//   * Java 的 (int) 强转是向零截断，Go 的 int(float64) 同样是向零截断，语义一致；
//   * Java 的 assert 在 Go 里用 panic 对应。
package main

import (
	"errors"
	"fmt"
	"sort"
)

// MaximumSupportedDistance LevenshteinAutomata.MAXIMUM_SUPPORTED_DISTANCE
const MaximumSupportedDistance = 2

// CharacterMaxCodePoint Character.MAX_CODE_POINT
const CharacterMaxCodePoint = 0x10FFFF

// ------------------------------------------------------------------ FuzzyQuery

// FuzzyQuery 构造参数与校验（源码四条 throw 全部复刻）。
type FuzzyQuery struct {
	Term           string
	MaxEdits       int
	PrefixLength   int
	MaxExpansions  int
	Transpositions bool
}

// FuzzyQuery 的默认值
const (
	DefaultMaxEdits         = MaximumSupportedDistance // = 2
	DefaultPrefixLength     = 0
	DefaultMaxExpansions    = 50
	DefaultTranspositions   = true
)

// NewFuzzyQuery 参数校验顺序与源码一致：maxEdits → prefixLength → maxExpansions。
func NewFuzzyQuery(term string, maxEdits, prefixLength, maxExpansions int,
	transpositions bool) (*FuzzyQuery, error) {
	if maxEdits < 0 || maxEdits > MaximumSupportedDistance {
		return nil, fmt.Errorf("maxEdits must be between 0 and %d", MaximumSupportedDistance)
	}
	if prefixLength < 0 {
		return nil, errors.New("prefixLength cannot be negative.")
	}
	if maxExpansions <= 0 {
		return nil, errors.New("maxExpansions must be positive.")
	}
	return &FuzzyQuery{term, maxEdits, prefixLength, maxExpansions, transpositions}, nil
}

// TermsEnumKind maxEdits == 0 时走 SingleTermsEnum（只能精确匹配），否则 FuzzyTermsEnum。
func (q *FuzzyQuery) TermsEnumKind() string {
	if q.MaxEdits == 0 {
		return "SingleTermsEnum"
	}
	return "FuzzyTermsEnum"
}

// FloatToEdits 把「最小相似度」换算成编辑距离。
//
//	similarity >= 1f → min(similarity, 2)
//	similarity == 0f → 0（源码注释：0 means exact, not infinite # of edits!）
//	否则             → min((int)((1 - similarity) * termLen), 2)
func FloatToEdits(minimumSimilarity float32, termLen int) int {
	if minimumSimilarity >= 1.0 {
		return int(min(float64(minimumSimilarity), MaximumSupportedDistance))
	}
	if minimumSimilarity == 0.0 {
		return 0
	}
	return min(int(float64(1.0-minimumSimilarity)*float64(termLen)), MaximumSupportedDistance)
}

// ---------------------------------------------------- ParametricDescription

// ParametricDescription 参数化描述：状态数 / 接受态 / 位置。
type ParametricDescription struct {
	W          int
	N          int
	MinErrors  []int
}

// Size minErrors.length * (w + 1)
func (d *ParametricDescription) Size() int { return len(d.MinErrors) * (d.W + 1) }

// IsAccept 接受条件 `w - offset + minErrors[state] <= n`
func (d *ParametricDescription) IsAccept(absState int) bool {
	state := absState / (d.W + 1)
	offset := absState % (d.W + 1)
	if offset < 0 {
		panic("offset must be >= 0")
	}
	return d.W-offset+d.MinErrors[state] <= d.N
}

// GetPosition 最小边界函数：absState % (w + 1)
func (d *ParametricDescription) GetPosition(absState int) int {
	return absState % (d.W + 1)
}

// AcceptStates 所有接受态。
func (d *ParametricDescription) AcceptStates() []int {
	out := []int{}
	for s := 0; s < d.Size(); s++ {
		if d.IsAccept(s) {
			out = append(out, s)
		}
	}
	return out
}

// NewLev1 super(w, 1, new int[] {0, 1, 0, -1, -1})
func NewLev1(w int) *ParametricDescription {
	return &ParametricDescription{w, 1, []int{0, 1, 0, -1, -1}}
}

// NewLev1T super(w, 1, new int[] {0, 1, 0, -1, -1, -1})
func NewLev1T(w int) *ParametricDescription {
	return &ParametricDescription{w, 1, []int{0, 1, 0, -1, -1, -1}}
}

// NewLev2 super(w, 2, new int[] {0,1,2,0,1,-1,0,-1,0,-1,0,-1,-1,-1,-1,-2,
//
//	-1,-2,-1,-2,-1,-2,-2,-2,-2,-2,-2,-2,-2,-2})
func NewLev2(w int) *ParametricDescription {
	return &ParametricDescription{w, 2, []int{
		0, 1, 2, 0, 1, -1, 0, -1, 0, -1, 0, -1, -1, -1, -1, -2,
		-1, -2, -1, -2, -1, -2, -2, -2, -2, -2, -2, -2, -2, -2}}
}

// --------------------------------------------------------- LevenshteinAutomata

// LevenshteinAutomata 按源码顺序复刻构造过程。
type LevenshteinAutomata struct {
	Word        []int
	AlphaMax    int
	Alphabet    []int
	RangeLower  []int
	RangeUpper  []int
	NumRanges   int
	Descriptions [3]*ParametricDescription
}

// NewLevenshteinAutomata 抽字母表 → 算补集区间 → 选参数化描述。
//
// n=2 的 T 变体（Lev2TParametricDescription）本轮没取到源码，因此不做区分，
// n=2 一律用 Lev2 的骨架（状态数/接受判据只对非 T 变体严格成立）。
func NewLevenshteinAutomata(word []int, alphaMax int, withTranspositions bool) (*LevenshteinAutomata, error) {
	la := &LevenshteinAutomata{Word: word, AlphaMax: alphaMax}
	seen := map[int]bool{}
	for _, v := range word {
		if v > alphaMax {
			return nil, fmt.Errorf("alphaMax exceeded by symbol %d in word", v)
		}
		seen[v] = true
	}
	for v := range seen {
		la.Alphabet = append(la.Alphabet, v)
	}
	sort.Ints(la.Alphabet)

	lower := 0
	for _, higher := range la.Alphabet {
		if higher > lower {
			la.RangeLower = append(la.RangeLower, lower)
			la.RangeUpper = append(la.RangeUpper, higher-1)
			la.NumRanges++
		}
		lower = higher + 1
	}
	if lower <= alphaMax {
		la.RangeLower = append(la.RangeLower, lower)
		la.RangeUpper = append(la.RangeUpper, alphaMax)
		la.NumRanges++
	}

	w := len(word)
	lev1 := NewLev1(w)
	if withTranspositions {
		lev1 = NewLev1T(w)
	}
	la.Descriptions = [3]*ParametricDescription{nil, lev1, NewLev2(w)}
	return la, nil
}

// GetVector 特征向量 X(x, V)：从 pos 到 end 逐位左移，命中置 1。
func (la *LevenshteinAutomata) GetVector(x, pos, end int) int {
	vector := 0
	for i := pos; i < end; i++ {
		vector <<= 1
		if la.Word[i] == x {
			vector |= 1
		}
	}
	return vector
}

// VectorWindow toAutomaton 里用的窗口：end = pos + min(w - pos, 2n+1)。
func (la *LevenshteinAutomata) VectorWindow(x, pos, n int) (int, int) {
	end := pos + min(len(la.Word)-pos, 2*n+1)
	return la.GetVector(x, pos, end), end
}

// AutomatonPlan toAutomaton(n, prefix) 前半段能确定的量。
type AutomatonPlan struct {
	Kind           string
	Value          string
	Range          int
	NumStates      int
	NumTransitions int
	PrefixStates   int
	TotalStates    int
}

// ToAutomatonPlan n == 0 退化成 makeString(prefix + word)；n >= len(descriptions) 不支持。
func (la *LevenshteinAutomata) ToAutomatonPlan(n int, prefix string) AutomatonPlan {
	if n == 0 {
		s := ""
		for _, c := range la.Word {
			s += string(rune(c))
		}
		return AutomatonPlan{Kind: "makeString", Value: prefix + s}
	}
	if n >= len(la.Descriptions) {
		return AutomatonPlan{Kind: "unsupported"}
	}
	desc := la.Descriptions[n]
	return AutomatonPlan{
		Kind:           "dfa",
		Range:          2*n + 1,
		NumStates:      desc.Size(),
		NumTransitions: desc.Size() * min(1+2*n, len(la.Alphabet)),
		PrefixStates:   len([]rune(prefix)),
		TotalStates:    desc.Size() + len([]rune(prefix)),
	}
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
