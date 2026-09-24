// FST 与前缀补全 suggester —— Go 侧演示入口（与 python/main.py 同题）。
package main

import "fmt"

func main() {
	fmt.Println("[1] 权重编码 encode(w) = Integer.MAX_VALUE - w")
	for _, w := range []int{0, 10, 100, 1000} {
		fmt.Printf("    w=%-5d -> output1=%-11d 解码回 %d\n", w, Encode(w), Decode(Encode(w)))
	}

	pp := PayloadProcessor{Sep: defaultPayloadSep}
	pl := pp.Make([]byte("coffee"), 42)
	surface, idx := pp.ParseSurfaceForm(pl)
	doc, _ := ReadVInt(pl, idx+1)
	fmt.Printf("\n[2] payload = surface + sep + vint(docId)\n    %q -> surface=%q docId=%d\n",
		pl, surface, doc)
	fmt.Printf("    vint 长度 127->%d B 128->%d B 16384->%d B (最多 5)\n",
		len(WriteVInt(127)), len(WriteVInt(128)), len(WriteVInt(16384)))
	fmt.Printf("    POS_SEP=0x%04X HOLE=0x%04X MAX_GRAPH_EXPANSIONS=%d\n",
		posSep, hole, defaultGraphExpansions)

	fmt.Println("\n[3] 队列容量启发式")
	for _, c := range []struct {
		ratio float64
		filt  bool
		nd    int
	}{{1.0, false, 1000}, {0.5, false, 1000}, {1.0, true, 1000}} {
		q := GetMaxTopNQueueSize(10, c.nd, c.ratio, c.filt, 5)
		fmt.Printf("    liveRatio=%.1f filter=%-5v numDocs=%d -> queueSize=%d\n",
			c.ratio, c.filt, c.nd, q)
	}
	fmt.Printf("    封顶 %d\n", maxTopNQueueSize)

	fst := NewSuggesterFST()
	fst.Add("coffee", 100, 1)
	fst.Add("coffee bean", 80, 2)
	fst.Add("coffee maker", 60, 3)
	fst.Add("caffeine", 75, 4)
	lk := &SuggestLookup{Fst: fst, MaxAnalyzedPathsPerOutput: 1}

	fmt.Printf("\n[4] 前缀补全（liveRatio(0 存活)=%.0f -> lookup 直接 return）\n",
		CalculateLiveDocRatio(0, 100))
	for _, prefix := range []string{"co", "ca", "zz"} {
		hits := lk.Lookup(prefix, 10, 100, 100, false, false)
		fmt.Printf("    %-4q -> %d 条\n", prefix, len(hits))
		for _, h := range hits {
			fmt.Printf("        w=%-4d doc=%d  %s\n", h.Weight, h.Doc, h.Surface)
		}
	}

	fst2 := NewSuggesterFST()
	fst2.Add("coffee", 100, 1)
	fst2.Add("coffee", 20, 9)
	lk2 := &SuggestLookup{Fst: fst2, MaxAnalyzedPathsPerOutput: 1}
	fmt.Printf("\n[5] 去重：不去重 %d 条，去重后 %d 条\n",
		len(lk2.Lookup("cof", 10, 100, 100, false, false)),
		len(lk2.Lookup("cof", 10, 100, 100, false, true)))
}
