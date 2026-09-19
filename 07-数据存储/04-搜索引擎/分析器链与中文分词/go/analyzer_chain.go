// ES/Lucene 分析器链与中文分词(Go),与 python/analyzer_chain.py 同构。
//
// 权威来源(实际读过):
//   1. .../analyzer-anatomy.html —— analyzer = 0+ char filter → 1 tokenizer → 0+ token filter,
//      顺序作用;tokenizer 记录 position/offset;token filter 不得改 position/offset
//   2. .../analysis-analyzers.html —— standard analyzer 按 Unicode 文本切分算法分词
//   3. .../index-modules-similarity.html —— BM25 k1=1.2 b=0.75;discount_overlaps 默认 true,
//      position increment 为 0 的 overlap token 不计入 norm
//   4. .../norms.html —— norm ~1 byte/doc/field
//   5. IKSegmenter.java —— 4 个 ISegmenter + IKArbitrator.process(ctx, cfg.isUseSmart())
package main

import (
	"fmt"
	"math"
	"os"
	"regexp"
	"strings"
)

const (
	k1         = 1.2
	b          = 0.75
	normsBytes = 1
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

// Token 词元:term + 字符 offset + position + positionIncrement
type Token struct {
	Term   string
	Start  int
	End    int
	Pos    int
	PosInc int
}

var (
	tagRe    = regexp.MustCompile(`<[^>]+>`)
	wordRe   = regexp.MustCompile(`[0-9A-Za-z]+`)
	lowercase = strings.ToLower
)

func cfHTMLStrip(s string) string { return tagRe.ReplaceAllString(s, " ") }

// tkStandard 分词器:字母数字连续段;首个 token 的 positionIncrement 也是 1
func tkStandard(s string) []Token {
	out := []Token{}
	pos := 0
	for _, m := range wordRe.FindAllStringIndex(s, -1) {
		out = append(out, Token{s[m[0]:m[1]], m[0], m[1], pos, 1})
		pos++
	}
	return out
}

func tfLowercase(ts []Token) []Token {
	out := make([]Token, 0, len(ts))
	for _, t := range ts {
		out = append(out, Token{lowercase(t.Term), t.Start, t.End, t.Pos, t.PosInc})
	}
	return out
}

// tfStop 去停用词:存活 token 的 position 原样保留(留下位置空洞,不重排号)
func tfStop(ts []Token, stops map[string]bool) []Token {
	out := []Token{}
	for _, t := range ts {
		if !stops[t.Term] {
			out = append(out, t)
		}
	}
	return out
}

// tfSynonym 同义词:与原词同占一个 position ⇒ PosInc=0(即 discount_overlaps 处理的对象)
func tfSynonym(ts []Token, syn map[string][]string) []Token {
	out := []Token{}
	for _, t := range ts {
		out = append(out, t)
		for _, s := range syn[t.Term] {
			out = append(out, Token{s, t.Start, t.End, t.Pos, 0})
		}
	}
	return out
}

// ---------------------------------------------------------------- 中文分词
var ikDict = []string{"中华人民共和国", "人民共和国", "中华", "人民", "共和国",
	"共和", "国", "成立", "了", "万岁"}

func reachable(s string) []bool {
	rs := []rune(s)
	n := len(rs)
	r := make([]bool, n+1)
	r[n] = true
	for i := n - 1; i >= 0; i-- {
		tail := string(rs[i:])
		for _, w := range ikDict {
			if strings.HasPrefix(tail, w) && r[i+len([]rune(w))] {
				r[i] = true
				break
			}
		}
	}
	return r
}

// ikMaxWord 输出所有能参与某种完整切分的词元(细粒度、互相重叠)
func ikMaxWord(s string) []string {
	rs := []rune(s)
	r := reachable(s)
	out := []string{}
	for i := 0; i < len(rs); i++ {
		tail := string(rs[i:])
		for _, w := range ikDict {
			if strings.HasPrefix(tail, w) && r[i+len([]rune(w))] {
				out = append(out, w)
			}
		}
	}
	return out
}

// ikSmart 最长匹配 greedy,只留一条路径
func ikSmart(s string) []string {
	rs := []rune(s)
	out := []string{}
	for i := 0; i < len(rs); {
		best := ""
		tail := string(rs[i:])
		for _, w := range ikDict {
			if strings.HasPrefix(tail, w) && len([]rune(w)) > len([]rune(best)) {
				best = w
			}
		}
		if best == "" {
			best = string(rs[i])
		}
		out = append(out, best)
		i += len([]rune(best))
	}
	return out
}

// ---------------------------------------------------------------- BM25
func bm25(tf float64, dl, avgdl, n, df float64, bb float64) float64 {
	idf := math.Log(1.0 + (n-df+0.5)/(df+0.5))
	return idf * (tf * (k1 + 1.0)) / (tf + k1*(1.0-bb+bb*dl/avgdl))
}

// normLength discount_overlaps=true 时跳过 PosInc==0 的重叠词
func normLength(ts []Token, discount bool) int {
	if !discount {
		return len(ts)
	}
	c := 0
	for _, t := range ts {
		if t.PosInc != 0 {
			c++
		}
	}
	return c
}

func main() {
	fmt.Println("== Demo 1 · char filter → tokenizer → token filter ==")
	toks := tfSynonym(
		tfStop(tfLowercase(tkStandard(cfHTMLStrip("<b>The</b> Quick brown-fox"))),
			map[string]bool{"the": true, "a": true, "is": true}),
		map[string][]string{"quick": {"fast"}})
	for _, t := range toks {
		fmt.Printf("   %-8q offset=[%d,%d) pos=%d inc=%d\n", t.Term, t.Start, t.End, t.Pos, t.PosInc)
	}
	check(len(toks) >= 4, "html_strip 先跑 ⇒ 标签里的 b 不会成为 token")
	hasFast := false
	for _, t := range toks {
		if t.Term == "fast" {
			hasFast = true
		}
	}
	check(hasFast, "同义词 quick→fast 展开成功")

	fmt.Println("\n== Demo 2 · token filter 不得改 position / offset ==")
	raw := tkStandard("The Quick brown fox")
	kept := tfLowercase(raw)
	same := true
	for i := range raw {
		if raw[i].Start != kept[i].Start || raw[i].End != kept[i].End || raw[i].Pos != kept[i].Pos {
			same = false
		}
	}
	check(same, "lowercase 后 offset 与 position 逐个不变")
	dropped := tfStop(kept, map[string]bool{"the": true})
	check(len(dropped) == 3 && dropped[0].Pos == 1 && dropped[2].Pos == 3,
		"停用词留下位置空洞:position 仍是 1,2,3")

	fmt.Println("\n== Demo 3 · ik_max_word vs ik_smart ==")
	s := "中华人民共和国成立了"
	cand, path := ikMaxWord(s), ikSmart(s)
	fmt.Println("   max_word:", strings.Join(cand, " / "))
	fmt.Println("   smart   :", strings.Join(path, " / "))
	check(len(cand) > len(path), "max_word 词元数多于 smart")
	check(strings.Join(path, "/") == "中华人民共和国/成立/了", "smart 走最长匹配单路径")

	fmt.Println("\n== Demo 4 · 分词粒度改变 dl ⇒ 改变 BM25 ==")
	d1 := []string{"中华人民共和国成立了", "中华人民共和国万岁"}
	dlS := float64(len(ikSmart(d1[0])))
	dlM := float64(len(ikMaxWord(d1[0])))
	avgS := (float64(len(ikSmart(d1[0]))) + float64(len(ikSmart(d1[1])))) / 2
	avgM := (float64(len(ikMaxWord(d1[0]))) + float64(len(ikMaxWord(d1[1])))) / 2
	sS, sM := bm25(1, dlS, avgS, 2, 1, b), bm25(1, dlM, avgM, 2, 1, b)
	fmt.Printf("   smart   dl=%.0f avgdl=%.2f score=%.6f\n", dlS, avgS, sS)
	fmt.Printf("   maxword dl=%.0f avgdl=%.2f score=%.6f\n", dlM, avgM, sM)
	check(math.Abs(sS-sM) > 1e-9, "粒度不同 ⇒ dl/avgdl 不同 ⇒ 分数不同")
	check(bm25(1, 3, 6, 10, 2, b) > bm25(1, 9, 6, 10, 2, b), "b=0.75 下短文档 tf 分量更高")
	check(math.Abs(bm25(1, 3, 6, 10, 2, 0)-bm25(1, 9, 6, 10, 2, 0)) < 1e-9,
		"b=0 时长度归一化关闭 ⇒ dl=3 与 dl=9 同分")

	fmt.Println("\n== Demo 5 · discount_overlaps ==")
	base := tfSynonym(tkStandard("quick fox"), map[string][]string{"quick": {"fast"}})
	on, off := normLength(base, true), normLength(base, false)
	fmt.Printf("   tokens=%d  true→%d  false→%d\n", len(base), on, off)
	check(on == 2 && off == 3, "默认 true:0-increment 的 fast 不计入 norm")
	fmt.Printf("   norm 成本 ≈ %d 字节/文档/字段\n", normsBytes)

	fmt.Printf("\n断言 %d 通过 / %d 失败\n", okCount, failCount)
	if failCount > 0 {
		os.Exit(1)
	}
}
