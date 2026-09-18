// AutoFormat demo 自检入口：go run . （自检失败即 panic）
package main

import "fmt"

var (
	sscanf = []string{"main", "read_header", "sscanf"}
	sgets  = []string{"main", "read_header", "sgets"}
	parse  = []string{"main", "read_header", "parse_hdr"}
	copyM  = append(append([]string{}, sscanf...), "copy_method")
	copyU  = append(append([]string{}, sscanf...), "copy_uri")
	copyV  = append(append([]string{}, sscanf...), "copy_version")
)

type span struct {
	s, e int
	st   []string
}

func buildLog(msg []byte, spans []span) []Rec {
	log := []Rec{}
	for _, sp := range spans {
		for o := sp.s; o < sp.e; o++ {
			log = append(log, Rec{O: o, C: msg[o], S: sp.st, L: "buf"})
		}
	}
	return log
}

func check(label string, cond bool, detail string) {
	if !cond {
		panic("FAIL " + label + " " + detail)
	}
	fmt.Printf("  ok  %-50s %s\n", label, detail)
}

func fmtNodes(ns []*Node) string {
	s := ""
	for _, n := range ns {
		s += fmt.Sprintf("(%d,%d) ", n.Lo, n.Hi)
	}
	return s
}

func main() {
	line1 := []byte("GET /news.html HTTP/1.0\r\n")
	h1 := []byte("User-Agent: Wget/1.10.2\r\n")
	h2 := []byte("Accept: */*\r\n")
	h3 := []byte("Host: 1.2.3.4\r\n")
	msg := append(append(append(append([]byte{}, line1...), h1...), h2...), h3...)
	o1, o2 := len(line1), len(line1)+len(h1)
	o3 := o2 + len(h2)

	// 每个 header 单独一次 sgets，且紧随其后插一次关键字解析 ——
	// 解析段的栈不同，才把三条 header 在读序列里切开成三个节点
	spans := []span{
		{0, 3, copyM}, {4, 14, copyU}, {15, 23, copyV},
		{o1, o2, sgets}, {o1, o1 + 12, parse},
		{o2, o3, sgets}, {o2, o2 + 8, parse},
		{o3, len(msg), sgets}, {o3, o3 + 6, parse},
	}
	log := buildLog(msg, spans)

	check("连续相同记录被去重", len(dedup([]Rec{log[0], log[0]})) == 1, "")

	tree := buildFieldTree(log, len(msg))
	top := tree.Children
	check("顶层 6 个节点", len(top) == 6, fmtNodes(top))
	check("最后一段被冲刷（论文伪代码漏了这一步）",
		top[len(top)-1].Hi == len(msg), fmt.Sprintf("hi=%d", top[len(top)-1].Hi))

	hdr1 := top[3]
	check("header 1 内部长出层次节点", len(hdr1.Children) == 1 &&
		hdr1.Children[0].Hi == o1+12, fmtNodes(hdr1.Children))

	check("Method/URI 历史首个栈帧就不同 → 不相似",
		!similar(historyOf(log, 0), historyOf(log, 4), hSimilar), "")
	check("三个 header 历史完全相同 → 相似",
		similar(historyOf(log, o1), historyOf(log, o2), hSimilar), "")
	ha := [][]string{{"a"}, {"a", "b"}, {"a", "b", "c"}, {"a", "b", "c", "d1"}}
	hb := [][]string{{"a"}, {"a", "b"}, {"a", "b", "c"}, {"a", "b", "c", "d2"}}
	check("共享前缀 3/4=75% 在 h=80 下不相似", !similar(ha, hb, hSimilar), "")
	check("同一对历史在 h=70 下变相似", similar(ha, hb, 70), "")

	markParallel(tree, log)
	pars := []*Node{}
	for _, c := range tree.Children {
		if c.Parallel {
			pars = append(pars, c)
		}
	}
	check("恰好 1 个并行字段节点", len(pars) == 1, fmtNodes(pars))
	check("并行字段含 3 个候选 header", len(pars[0].Children) == 3,
		fmtNodes(pars[0].Children))
	check("Method/URI/Version 未被并入并行字段", len(tree.Children) == 4,
		fmtNodes(tree.Children))

	lists := sequentialFields(tree)
	check("顶层顺序字段 = 4 个", len(lists[0]) == 4, fmtNodes(lists[0]))
	check("并行字段内部再走一遍 → 3 项", len(lists[1]) == 3, fmtNodes(lists[1]))
	fmt.Println("AutoFormat(Go): ALL ASSERTIONS PASSED")
}
