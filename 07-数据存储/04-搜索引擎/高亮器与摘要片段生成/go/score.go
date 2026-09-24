// Passage 与 PassageScorer：把每个 passage 当成一篇小文档打分。
//
// 转写自 apache/lucene@main
//   lucene/highlighter/src/java/org/apache/lucene/search/uhighlight/Passage.java
//   lucene/highlighter/src/java/org/apache/lucene/search/uhighlight/PassageScorer.java
//
// 语言差异（已在代码中显式落地）：
//   * Java 的 float 全程是 binary32，Go 侧一律 float32，运算点逐个显式转换；
//   * Java 的 Math.log 收 double 返回 double，Go 的 math.Log 也是 float64，
//     所以「先转 float64 再算、算完转回 float32」与源码的隐式拓宽一致；
//   * score() 的累加器在 Java 里是 double，Go 侧保持 float64，最后一步才转 float32。
package main

import (
	"fmt"
	"math"
	"strings"
)

// Match 是一次命中：起止偏移、term、该 term 在全文中的频次。
type Match struct {
	Start, End   int
	Term         string
	FreqInDoc    int
}

// Passage 一段候选摘要。
type Passage struct {
	StartOffset int
	EndOffset   int
	Score       float32
	Matches     []Match
}

// NewPassage 对应 Java 的字段初值：startOffset = endOffset = -1。
func NewPassage() *Passage {
	return &Passage{StartOffset: -1, EndOffset: -1, Score: 0}
}

func (p *Passage) AddMatch(start, end int, term string, freqInDoc int) {
	if !(p.StartOffset <= start && start <= p.EndOffset) {
		panic("match not inside passage")
	}
	p.Matches = append(p.Matches, Match{start, end, term, freqInDoc})
}

func (p *Passage) Reset() {
	p.StartOffset = -1
	p.EndOffset = -1
	p.Score = 0
	p.Matches = p.Matches[:0]
}

func (p *Passage) NumMatches() int { return len(p.Matches) }

// Length 对应 getLength() = endOffset - startOffset。
func (p *Passage) Length() int { return p.EndOffset - p.StartOffset }

// String 照抄 Passage.toString：Passage[0-22]{yin[0-3],yang[4-8]}score=2.4964213
func (p *Passage) String() string {
	var b strings.Builder
	fmt.Fprintf(&b, "Passage[%d-%d]{", p.StartOffset, p.EndOffset)
	for i, m := range p.Matches {
		if i != 0 {
			b.WriteString(",")
		}
		fmt.Fprintf(&b, "%s[%d-%d]", m.Term,
			m.Start-p.StartOffset, m.End-p.StartOffset)
	}
	fmt.Fprintf(&b, "}score=%v", p.Score)
	return b.String()
}

// PassageScorer 默认 k1=1.2、b=0.75、pivot=87（87 是英语句子的典型长度）。
// 源码注释自己都写了 "this formula is completely made up"：数值没有"正确答案"，
// 但每一步的舍入是确定的，所以这里严格保持 float32。
type PassageScorer struct {
	K1, B, Pivot float32
}

func NewPassageScorer() *PassageScorer {
	return &PassageScorer{K1: 1.2, B: 0.75, Pivot: 87}
}

// Weight 词的重要度：用「内容长度 / pivot」近似 numDocs，再套一个 DFR 味的 log。
// Java 侧 `1 + contentLength / pivot` 是 int / float 的 float 除法。
func (s *PassageScorer) Weight(contentLength, totalTermFreq int) float32 {
	numDocs := float32(1.0) + float32(contentLength)/s.Pivot
	w := float32(math.Log(1.0 + (float64(numDocs)+0.5)/(float64(totalTermFreq)+0.5)))
	return (s.K1 + 1.0) * w
}

// Tf passage 内的词频饱和函数，带长度归一化。
func (s *PassageScorer) Tf(freq, passageLen int) float32 {
	norm := s.K1 * ((1.0 - s.B) + s.B*(float32(passageLen)/s.Pivot))
	return float32(freq) / (float32(freq) + norm)
}

// Norm 位置加成：越靠前的 passage 越像摘要。1 + 1/log(pivot + start)。
func (s *PassageScorer) Norm(passageStart int) float32 {
	return 1.0 + 1.0/float32(math.Log(float64(s.Pivot+float32(passageStart))))
}

// Score Σ tf(词在 passage 内频次, passage 长度) * weight(全文长度, 词在全文频次)，再乘 norm。
//
// 同一个词在 passage 里出现多次只算一个「词」，但 tf 用 passage 内的出现次数；
// freqInDoc 取第一次遇到该词时记录的全文频次。
func (s *PassageScorer) Score(p *Passage, contentLength int) float32 {
	var score float64
	indexOf := map[string]int{}
	var freqsInPassage, freqsInDoc []int
	for _, m := range p.Matches {
		ti, ok := indexOf[m.Term]
		if !ok {
			ti = len(freqsInPassage)
			indexOf[m.Term] = ti
			freqsInPassage = append(freqsInPassage, 0)
			freqsInDoc = append(freqsInDoc, m.FreqInDoc)
		}
		freqsInPassage[ti]++
	}
	for i := range freqsInPassage {
		t := s.Tf(freqsInPassage[i], p.Length())
		w := s.Weight(contentLength, freqsInDoc[i])
		score += float64(t * w) // float*float -> float，再加到 double
	}
	score *= float64(s.Norm(p.StartOffset))
	return float32(score)
}
