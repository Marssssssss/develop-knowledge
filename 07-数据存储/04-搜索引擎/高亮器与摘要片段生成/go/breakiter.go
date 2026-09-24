// BreakIterator 与 LengthGoalBreakIterator。
//
// 转写自 apache/lucene@main
//   lucene/highlighter/src/java/org/apache/lucene/search/uhighlight/LengthGoalBreakIterator.java
//
// java.text.BreakIterator 这里用「一组有序边界点」建模，0 与 endIndex 恒为边界。
// 语义对齐 JDK 文档：
//   first()/last()  首个/末个边界
//   next()          current 之后的下一个边界，没有则 DONE
//   preceding(off)  **严格小于** off 的最后一个边界，没有则 DONE
//   following(off)  **严格大于** off 的第一个边界，没有则 DONE
package main

import "sort"

// DONE 对应 BreakIterator.DONE。
const DONE = -1

// BreakIterator 句子/整段边界迭代器（stateful，SetText 会重置游标）。
type BreakIterator struct {
	bounds []int
	end    int
	cur    int
}

// NewBreakIterator inner 是位于 (0, textLen) 开区间内的内部边界点。
func NewBreakIterator(textLen int, inner []int) *BreakIterator {
	b := &BreakIterator{end: textLen}
	seen := map[int]bool{}
	b.bounds = append(b.bounds, 0)
	for _, i := range inner {
		if i > 0 && i < textLen && !seen[i] {
			seen[i] = true
			b.bounds = append(b.bounds, i)
		}
	}
	b.bounds = append(b.bounds, textLen)
	sort.Ints(b.bounds)
	b.cur = 0
	return b
}

func (b *BreakIterator) SetText(s string)   { b.end = len(s); b.cur = 0 }
func (b *BreakIterator) TextLength() int    { return b.end }
func (b *BreakIterator) Current() int       { return b.cur }
func (b *BreakIterator) Bounds() []int      { return b.bounds }

func (b *BreakIterator) First() int { b.cur = b.bounds[0]; return b.cur }

func (b *BreakIterator) Last() int { b.cur = b.bounds[len(b.bounds)-1]; return b.cur }

func (b *BreakIterator) Next() int {
	for _, x := range b.bounds {
		if x > b.cur {
			b.cur = x
			return x
		}
	}
	return DONE
}

func (b *BreakIterator) Preceding(offset int) int {
	best := DONE
	for _, x := range b.bounds {
		if x < offset {
			best = x
		} else {
			break
		}
	}
	if best != DONE {
		b.cur = best
	}
	return best
}

func (b *BreakIterator) Following(offset int) int {
	for _, x := range b.bounds {
		if x > offset {
			b.cur = x
			return x
		}
	}
	return DONE
}

// LengthGoalBreakIterator 包装另一个 BreakIterator，跳过会让 passage 过短的断点。
//
//   isMinimumLength=true  → 只取「不低于目标」的一侧（永不欠冲）
//   isMinimumLength=false → 取离目标最近的那一侧（closest-to-length）
type LengthGoalBreakIterator struct {
	base              *BreakIterator
	lengthGoal        int
	fragmentAlignment float32
	isMinimumLength   bool
	cur               int
}

func newLengthGoal(base *BreakIterator, lengthGoal int, fa float32, isMin bool) *LengthGoalBreakIterator {
	if fa < 0 || fa > 1 || fa != fa {
		panic("fragmentAlignment must be >= zero and <= one")
	}
	return &LengthGoalBreakIterator{base: base, lengthGoal: lengthGoal,
		fragmentAlignment: fa, isMinimumLength: isMin, cur: base.Current()}
}

func CreateMinLength(base *BreakIterator, minLength int, fa float32) *LengthGoalBreakIterator {
	return newLengthGoal(base, minLength, fa, true)
}

func CreateClosestToLength(base *BreakIterator, target int, fa float32) *LengthGoalBreakIterator {
	return newLengthGoal(base, target, fa, false)
}

func (g *LengthGoalBreakIterator) TextLength() int { return g.base.TextLength() }
func (g *LengthGoalBreakIterator) Current() int    { return g.cur }

func (g *LengthGoalBreakIterator) SetText(s string) {
	g.base.SetText(s)
	g.cur = g.base.Current()
}

func (g *LengthGoalBreakIterator) First() int { g.cur = g.base.First(); return g.cur }
func (g *LengthGoalBreakIterator) Last() int  { g.cur = g.base.Last(); return g.cur }

// Next 对应 Java 的 return following(currentCache, currentCache + lengthGoal)
func (g *LengthGoalBreakIterator) Next() int {
	return g.following2(g.cur, g.cur+g.lengthGoal)
}

// Following targetIdx = (matchEndIndex + 1) + (int)(lengthGoal * (1.f - fragmentAlignment))
func (g *LengthGoalBreakIterator) Following(matchEndIndex int) int {
	// Java: (int)(lengthGoal * (1.f - fragmentAlignment))，int 与 float 相乘后截断
	d := int(float32(g.lengthGoal) * (1.0 - g.fragmentAlignment))
	return g.following2(matchEndIndex, (matchEndIndex+1)+d)
}

func (g *LengthGoalBreakIterator) following2(matchEndIndex, targetIdx int) int {
	if targetIdx >= g.TextLength() {
		if g.cur == g.base.Last() {
			return DONE
		}
		g.cur = g.base.Last()
		return g.cur
	}
	afterIdx := g.base.Following(targetIdx - 1)
	if afterIdx == DONE {
		g.cur = g.base.Last()
		return DONE
	}
	if afterIdx == targetIdx { // right on the money
		g.cur = afterIdx
		return g.cur
	}
	if g.isMinimumLength { // thus never undershoot
		g.cur = afterIdx
		return g.cur
	}
	beforeIdx := g.base.Preceding(targetIdx)
	if targetIdx-beforeIdx < afterIdx-targetIdx && beforeIdx > matchEndIndex {
		g.cur = beforeIdx
		return g.cur
	}
	g.cur = afterIdx
	return g.cur
}

// Preceding targetIdx = (matchStartIndex - 1) - (int)(lengthGoal * fragmentAlignment)
func (g *LengthGoalBreakIterator) Preceding(matchStartIndex int) int {
	delta := int(float32(g.lengthGoal) * g.fragmentAlignment)
	targetIdx := (matchStartIndex - 1) - delta
	if targetIdx <= 0 {
		if g.cur == g.base.First() {
			return DONE
		}
		g.cur = g.base.First()
		return g.cur
	}
	beforeIdx := g.base.Preceding(targetIdx + 1)
	if beforeIdx == DONE {
		g.cur = g.base.First()
		return DONE
	}
	if beforeIdx == targetIdx { // right on the money
		g.cur = beforeIdx
		return g.cur
	}
	if g.isMinimumLength { // thus never undershoot
		g.cur = beforeIdx
		return g.cur
	}
	afterIdx := g.base.Following(targetIdx - 1)
	if afterIdx-targetIdx < targetIdx-beforeIdx && afterIdx < matchStartIndex {
		g.cur = afterIdx
		return g.cur
	}
	g.cur = beforeIdx
	return g.cur
}
