// 高亮器与摘要片段生成 —— Go 侧演示入口。
//
// 与 python/main.py 同题：偏移源 → 切 passage → 打分择优 → 格式化。
// 本机无 Go 工具链，本文件只作等价实现与人工审查用。
package main

import (
	"fmt"
	"strings"
)

const content = "Lucene is a search library. Lucene builds an inverted index. " +
	"Highlighters cut passages from matched offsets. The best passages win."

func loc(word string) Match {
	i := strings.Index(content, word)
	return Match{Start: i, End: i + len(word), Term: word, FreqInDoc: 1}
}

func main() {
	bounds := []int{}
	for i := 0; ; {
		j := strings.Index(content[i:], ". ")
		if j < 0 {
			break
		}
		i = i + j + 2
		bounds = append(bounds, i)
	}

	fmt.Println("正文:", content)
	fmt.Println("句子边界:", append(append([]int{0}, bounds...), len(content)))

	scorer := NewPassageScorer()
	fmt.Printf("\nPassageScorer k1=%.1f b=%.2f pivot=%.0f\n", scorer.K1, scorer.B, scorer.Pivot)
	fmt.Printf("  weight(全文%d, 词频2) = %.6f\n", len(content), scorer.Weight(len(content), 2))
	fmt.Printf("  tf(词频2, 段长20)     = %.6f\n", scorer.Tf(2, 20))
	fmt.Printf("  norm(0) = %.6f   norm(100) = %.6f\n", scorer.Norm(0), scorer.Norm(100))

	matches := []Match{
		loc("Lucene"), loc("inverted"), loc("index"),
		loc("Highlighters"), loc("passages"),
	}

	bi := NewBreakIterator(len(content), bounds)
	fh := NewFieldHighlighter("body", bi, scorer, 3)
	passages := fh.HighlightOffsetsEnums(NewOffsetsEnum(matches))
	fmt.Println("\n切成", len(passages), "段:")
	for _, p := range passages {
		fmt.Println("  ", p.String())
	}

	fmt.Println("\n格式化:")
	fmt.Println("  ", fh.Formatter.Format(passages, content))

	bi1 := NewBreakIterator(len(content), bounds)
	fh1 := NewFieldHighlighter("body", bi1, scorer, 1)
	best := fh1.HighlightOffsetsEnums(NewOffsetsEnum(matches))
	fmt.Printf("\nmaxPassages=1 只留最高分: [%d,%d) score=%.6f\n",
		best[0].StartOffset, best[0].EndOffset, best[0].Score)

	// LengthGoalBreakIterator 两种模式的分歧
	base := NewBreakIterator(60, []int{10, 20, 30, 40, 50})
	mn := CreateMinLength(base, 10, 0.5)
	base2 := NewBreakIterator(60, []int{10, 20, 30, 40, 50})
	cl := CreateClosestToLength(base2, 10, 0.5)
	fmt.Printf("\nlengthGoal=10 alignment=0.5  preceding(23): min=%d closest=%d\n",
		mn.Preceding(23), cl.Preceding(23))
}
