// FieldHighlighter：把命中偏移流切成 passage、打分、择优，再格式化成摘要片段。
//
// 转写自 apache/lucene@main
//   .../uhighlight/FieldHighlighter.java        （highlightOffsetsEnums / maybeAddPassage /
//                                                 getSummaryPassagesNoHighlight）
//   .../uhighlight/DefaultPassageFormatter.java （format：重叠命中合并 + 省略号）
//
// 关键口径（全部来自源码，非推测）：
//   * 择优用小顶堆，堆顶是当前最差的那个；新 passage 打不过堆顶就 reset 复用（对象池），
//     所以 Passage 实例数 ≤ maxPassages + 1。
//   * 一个匹配若横跨内容末尾（start < contentLength && end > contentLength）会被整条丢弃。
//   * startOffset == -1 表示字段没按 offsets 索引，Lucene 直接抛 IllegalArgumentException，
//     Go 侧用 panic 对应。
package main

import (
	"fmt"
	"sort"
	"strings"
)

// OffsetsEnum 命中偏移流（本 demo 用切片代替 Lucene 的迭代器）。
type OffsetsEnum struct {
	pos []Match
	i   int
}

func NewOffsetsEnum(m []Match) *OffsetsEnum { return &OffsetsEnum{pos: m, i: -1} }

func (o *OffsetsEnum) NextPosition() bool {
	o.i++
	return o.i < len(o.pos)
}

func (o *OffsetsEnum) Cur() Match { return o.pos[o.i] }

// passageQueue 小顶堆：score 升序，同分按 startOffset 升序（照抄 Java comparator）。
type passageQueue []*Passage

func (q passageQueue) Len() int { return len(q) }

func (q passageQueue) Less(i, j int) bool {
	if q[i].Score != q[j].Score {
		return q[i].Score < q[j].Score
	}
	return q[i].StartOffset < q[j].StartOffset
}

func (q passageQueue) Swap(i, j int) { q[i], q[j] = q[j], q[i] }

// 注：不引入 container/heap —— 这里只需要「随时能取到最小值」，排序切片已足够，
// 且 sort 后的 [0] 就是小顶堆堆顶。

// FieldHighlighter 单字段高亮器。
type FieldHighlighter struct {
	Field                   string
	Bi                      *BreakIterator
	Scorer                  *PassageScorer
	MaxPassages             int
	MaxNoHighlightPassages  int
	Formatter               *DefaultPassageFormatter
}

func NewFieldHighlighter(field string, bi *BreakIterator, scorer *PassageScorer,
	maxPassages int) *FieldHighlighter {
	return &FieldHighlighter{Field: field, Bi: bi, Scorer: scorer,
		MaxPassages: maxPassages, MaxNoHighlightPassages: -1,
		Formatter: NewDefaultPassageFormatter("<b>", "</b>", "... ", false)}
}

// HighlightFieldForDoc 主入口：偏移流 → passage → 兜底 → 格式化。
func (f *FieldHighlighter) HighlightFieldForDoc(off *OffsetsEnum, content string) string {
	if len(content) == 0 {
		return ""
	}
	f.Bi.SetText(content)
	passages := f.HighlightOffsetsEnums(off)
	if len(passages) == 0 {
		n := f.MaxPassages
		if f.MaxNoHighlightPassages != -1 {
			n = f.MaxNoHighlightPassages
		}
		passages = f.GetSummaryPassagesNoHighlight(n)
	}
	if len(passages) > 0 {
		return f.Formatter.Format(passages, content)
	}
	return ""
}

// GetSummaryPassagesNoHighlight 没有命中时的兜底摘要：取前 N 段，这些 Passage 无 match。
func (f *FieldHighlighter) GetSummaryPassagesNoHighlight(maxPassages int) []*Passage {
	f.Bi.First()
	var out []*Passage
	pos := f.Bi.Current()
	for len(out) < maxPassages {
		nxt := f.Bi.Next()
		if nxt == DONE {
			break
		}
		p := NewPassage()
		p.StartOffset = pos
		p.EndOffset = nxt
		out = append(out, p)
		pos = nxt
	}
	return out
}

// HighlightOffsetsEnums 核心：切分 + 打分 + 择优。
func (f *FieldHighlighter) HighlightOffsetsEnums(off *OffsetsEnum) []*Passage {
	contentLength := f.Bi.TextLength()
	if !off.NextPosition() {
		return nil
	}
	var q passageQueue
	p := NewPassage()
	lastPassageEnd := 0

	for {
		m := off.Cur()
		if m.Start == -1 {
			panic(fmt.Sprintf("field '%s' was indexed without offsets, cannot highlight", f.Field))
		}
		if m.Start < contentLength && m.End > contentLength {
			if !off.NextPosition() {
				break
			}
			continue
		}
		if m.Start >= p.EndOffset {
			p = f.maybeAddPassage(&q, p, contentLength)
			if m.Start >= contentLength {
				break
			}
			// 从命中「中点」往两侧找断点，让片段长度更接近 fragsize
			center := m.Start + (m.End-m.Start)/2
			lo := max(f.Bi.Preceding(max(m.Start+1, center)), lastPassageEnd)
			p.StartOffset = min(m.Start, lo)
			lastPassageEnd = max(m.End, min(f.Bi.Following(min(m.End-1, center)), contentLength))
			p.EndOffset = lastPassageEnd
		}
		p.AddMatch(m.Start, m.End, m.Term, m.FreqInDoc)
		if !off.NextPosition() {
			break
		}
	}
	f.maybeAddPassage(&q, p, contentLength)

	out := []*Passage(q)
	sort.Slice(out, func(i, j int) bool { return out[i].StartOffset < out[j].StartOffset })
	return out
}

func (f *FieldHighlighter) maybeAddPassage(q *passageQueue, p *Passage, contentLength int) *Passage {
	if p.StartOffset == -1 {
		return p // 空 passage，忽略
	}
	p.Score = f.Scorer.Score(p, contentLength)
	if q.Len() == f.MaxPassages && p.Score < (*q)[0].Score {
		p.Reset() // 打不过最差的，复用这个对象
		return p
	}
	// 入堆后若超容，弹出堆顶（最差）并复用
	*q = append(*q, p)
	sort.Sort(*q)
	if q.Len() > f.MaxPassages {
		worst := (*q)[0]
		*q = (*q)[1:]
		worst.Reset()
		return worst
	}
	return NewPassage()
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

func max(a, b int) int {
	if a > b {
		return a
	}
	return b
}

// DefaultPassageFormatter 默认格式器：<b> 加粗命中，段落之间用 "... " 连接。
type DefaultPassageFormatter struct {
	PreTag, PostTag, Ellipsis string
	Escape                    bool
}

// Go 的 string 不可为 nil，所以源码里针对 null 的判空在这里天然不存在；
// 空串是合法配置（等于不加标签）。
func NewDefaultPassageFormatter(pre, post, ell string, esc bool) *DefaultPassageFormatter {
	return &DefaultPassageFormatter{PreTag: pre, PostTag: post, Ellipsis: ell, Escape: esc}
}

// Format 把 passages 拼成字符串：重叠命中会合并，命中越出 passage 会被截断。
func (d *DefaultPassageFormatter) Format(passages []*Passage, content string) string {
	var sb strings.Builder
	pos := 0
	for _, p := range passages {
		if sb.Len() > 0 && p.StartOffset != pos {
			sb.WriteString(d.Ellipsis)
		}
		pos = p.StartOffset
		for i := 0; i < p.NumMatches(); i++ {
			start := p.Matches[i].Start
			sb.WriteString(d.slice(content, pos, start))
			end := p.Matches[i].End
			// 命中之间可能重叠：向前吞掉所有重叠区间，取最大的 end
			for i+1 < p.NumMatches() && p.Matches[i+1].Start < end {
				i++
				if p.Matches[i].End > end {
					end = p.Matches[i].End
				}
			}
			if end > p.EndOffset { // 命中可能越出 passage
				end = p.EndOffset
			}
			sb.WriteString(d.PreTag)
			sb.WriteString(d.slice(content, start, end))
			sb.WriteString(d.PostTag)
			pos = end
		}
		sb.WriteString(d.slice(content, pos, max(pos, p.EndOffset)))
		pos = p.EndOffset
	}
	return sb.String()
}

func (d *DefaultPassageFormatter) slice(content string, start, end int) string {
	if start < 0 {
		start = 0
	}
	if end > len(content) {
		end = len(content)
	}
	s := content[start:end]
	if !d.Escape {
		return s
	}
	s = strings.ReplaceAll(s, "&", "&amp;")
	s = strings.ReplaceAll(s, "<", "&lt;")
	return strings.ReplaceAll(s, ">", "&gt;")
}
