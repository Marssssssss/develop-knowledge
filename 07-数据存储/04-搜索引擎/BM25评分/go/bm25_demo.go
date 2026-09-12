# BM25 评分算法演示 —— Go 标准库版（与 Python 等价）
#
# 权威公式：Elastic blog *Practical BM25 — Part 2*
#
#     score(D, Q) = Σ_{q ∈ Q}  IDF(q) · (tf · (k1 + 1))
#                                          ───────────────────────
#                              (tf + k1 · (1 − b + b · |D|/avgdl))
#
#     IDF(q) = ln(1 + (N − n(q) + 0.5) / (n(q) + 0.5))
#
# 默认：k1 = 1.2, b = 0.75（与 Elasticsearch 完全一致）
package main

import (
	"fmt"
	"math"
	"strings"
)

const (
	K1 = 1.2
	B  = 0.75
)

// Doc 与 Python 版同源（Elastic blog Practical BM25 Part 2 文段）
type Doc struct {
	ID    int
	Title string
}

// Length 表示词数（去停用词后），即字段长度 |D|
func length(d Doc) int { return len(strings.Fields(strings.ToLower(d.Title))) }

// tokenize 朴素分词：lower + 空白切分
func tokenize(d Doc) []string { return strings.Fields(strings.ToLower(d.Title)) }

func buildIndex(docs []Doc) (postings map[string][]int, docLen []int, avgdl float64) {
	postings = map[string][]int{}
	for _, d := range docs {
		l := length(d)
		docLen = append(docLen, l)
		seen := map[string]bool{}
		for _, t := range tokenize(d) {
			if !seen[t] {
				seen[t] = true
				postings[t] = append(postings[t], d.ID)
			}
		}
	}
	sum := 0
	for _, v := range docLen {
		sum += v
	}
	avgdl = float64(sum) / float64(len(docLen))
	return
}

// IDF 公式：Lucene/Elasticsearch 的 BM25 IDF（与 TF·IDF 的 IDF 不同）
func idfLucene(nq, N int) float64 {
	return math.Log(1 + float64(N-nq+0.5)/float64(nq+0.5))
}

// bm25 对单个文档计算 sum_q IDF(q) · tf·(k1+1)/(tf+k1·norm)
func bm25(postings map[string][]int, docLen []int, avgdl float64, d Doc, terms []string) float64 {
	norm := 1 - B + B*float64(docLen[d.ID])/avgdl
	score := 0.0
	for _, q := range terms {
		posting, ok := postings[q]
		if !ok {
			continue
		}
		// 这里用 posting 数作为 n(q)
		nq := len(posting)
		tf := 0
		for _, t := range tokenize(d) {
			if t == q {
				tf++
			}
		}
		score += idfLucene(nq, len(docLen)) * float64(tf*(K1+1)) /
			float64(tf + K1*norm)
	}
	return score
}

func main() {
	Docs := []Doc{
		{ID: 0, Title: "Shane Connelly"},
		{ID: 1, Title: "Shane is the best"},
		{ID: 2, Title: "Connelly is a name"},
		{ID: 3, Title: "Shane and Connelly are names together"},
	}

	fmt.Println(strings.Repeat("=", 68))
	fmt.Println("Demo 1 · BM25 评分算法（Go 标准库版，与 Python 等价）")
	fmt.Println(strings.Repeat("=", 68))

	postings, docLen, avgdl := buildIndex(Docs)
	fmt.Printf("\n语料: %d 篇文档，平均长度 avgdl = %.3f\n", len(Docs), avgdl)
	fmt.Println("\n倒排表（term → 包含它的文档 id 列表）：")
	for _, term := range sortedKeys(postings) {
		fmt.Printf("  %-10s → n(%s)=%d docs postings=%v\n",
			term, term, len(postings[term]), postings[term])
	}

	// 查询 "shane connelly"
	query := []string{"shane", "connelly"}
	fmt.Printf("\n查询 query=%v (k1=1.2, b=0.75，ES 默认)\n", query)

	fmt.Println("\n  doc | len | tf(s)| tf(c)|  IDF(s)  | IDF(c)  | BM25 score")
	fmt.Println("  " + strings.Repeat("-", 76))
	type pair struct {
		id    int
		score float64
	}
	scores := make([]pair, 0, len(Docs))
	for _, d := range Docs {
		tfs, tfc := 0, 0
		for _, t := range tokenize(d) {
			switch t {
			case "shane":
				tfs++
			case "connelly":
				tfc++
			}
		}
		ids := idfLucene(len(postings["shane"]), len(Docs))
		idc := idfLucene(len(postings["connelly"]), len(Docs))
		s := bm25(postings, docLen, avgdl, d, query)
		scores = append(scores, pair{d.ID, s})
		fmt.Printf("  %3d | %3d | %4d | %4d | %+.3f | %+.3f | %+.4f\n",
			d.ID, docLen[d.ID], tfs, tfc, ids, idc, s)
	}
	ranked := make([]int, 0, len(scores))
	for i := 0; i < len(scores); i++ {
		for j := i + 1; j < len(scores); j++ {
			if scores[j].score > scores[i].score {
				scores[i], scores[j] = scores[j], scores[i]
			}
		}
	}
	for _, p := range scores {
		ranked = append(ranked, p.id)
	}
	fmt.Printf("\n排序结果（高 → 低）：%v\n", ranked)

	// Demo 2：词频饱和曲线
	fmt.Println("\n" + strings.Repeat("-", 68))
	fmt.Println("Demo 2 · 词频饱和曲线（tf saturation）")
	fmt.Println("假设 doc 长度等于 avgdl（norm=1），则 BM25 tf 部分 = tf·(k1+1)/(tf+k1)")
	fmt.Println("           TF·IDF tf 部分 = ln(tf+1)（无上界）\n")
	fmt.Println("   tf |   BM25  |  TF·IDF |  备注")
	fmt.Println("  " + strings.Repeat("-", 50))
	for tf := 1; tf <= 30; tf++ {
		bm := float64(tf*(K1+1)) / float64(tf+K1)
		tfi := math.Log(float64(tf + 1))
		note := ""
		if tf == int(K1) {
			note = "<-- BM25 半饱和点"
		}
		fmt.Printf("  %3d | %+.4f | %+.4f | %s\n", tf, bm, tfi, note)
	}

	// Demo 3：文档长度归一化
	fmt.Println("\n" + strings.Repeat("-", 68))
	fmt.Println("Demo 3 · 文档长度归一化 (b 参数)")
	fmt.Println("对 tf=1 的词，贡献 ∝ 1/(tf + k1·norm)，norm = 1 − b + b·|D|/avgdl")
	fmt.Printf("\n  假设 avgdl=4, k1=1.2\n\n")
	fmt.Println("   |D| |  b=0   |  b=0.5 | b=0.75 |  b=1.0 | 说明")
	fmt.Println("  " + strings.Repeat("-", 70))
	for _, dl := range []int{1, 2, 4, 8, 16, 32} {
		line := fmt.Sprintf("  %4d |", dl)
		for _, b := range []float64{0, 0.5, 0.75, 1.0} {
			norm := 1 - b + b*float64(dl)/4
			contrib := 1.0 / (1 + K1*norm)
			line += fmt.Sprintf(" %+.4f |", contrib)
		}
		switch {
		case dl < 4:
			line += " 短文档优势"
		case dl == 4:
			line += " 平均"
		default:
			line += " 长文档劣势"
		}
		fmt.Println(line)
	}
	fmt.Println("\n要点：b=0 时归一化失效（所有列相等）；b=1 时长文档被重度惩罚。")
}

// sortedKeys 提取 map 的 key 并排序（保持确定性输出）
func sortedKeys(m map[string][]int) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	for i := 1; i < len(keys); i++ {
		for j := i; j > 0 && keys[j-1] > keys[j]; j-- {
			keys[j-1], keys[j] = keys[j], keys[j-1]
		}
	}
	return keys
}
