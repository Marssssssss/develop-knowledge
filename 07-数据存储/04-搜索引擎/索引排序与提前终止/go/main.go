// 索引排序与提前终止 —— Go 侧演示入口（与 python/main.py 同题）。
package main

import "fmt"

func main() {
	fmt.Println("[1] 哪些 SortField 能做索引排序？")
	for _, t := range []string{TypeString, TypeInt, TypeLong, TypeDouble, TypeFloat,
		TypeScore, TypeDoc, TypeCustom, TypeRewritable, TypeStringVal} {
		field := "f"
		if t == TypeScore || t == TypeDoc {
			field = ""
		}
		sf, _ := newSortField(field, t, false, nil)
		fmt.Printf("    %-12s getIndexSorter()=%-5v needsScores()=%v\n",
			t, sf.GetIndexSorter(), sf.NeedsScores())
	}

	fmt.Println("\n[2] Sort 的两个常量")
	fmt.Printf("    RELEVANCE  = %s needsScores=%v\n", SortRelevance, SortRelevance.NeedsScores())
	fmt.Printf("    INDEXORDER = %s needsScores=%v\n", SortIndexOrder, SortIndexOrder.NeedsScores())

	fmt.Println("\n[3] SetIndexSort：有一个字段不可用就整体拒绝")
	cfg := NewIndexWriterConfig()
	ia, _ := newSortField("a", TypeInt, false, nil)
	ib, _ := newSortField("b", TypeString, false, nil)
	for _, c := range []struct {
		name string
		s    *Sort
	}{{"INDEXORDER(DOC)", SortIndexOrder}, {"RELEVANCE(SCORE)", SortRelevance},
		{"[INT, SCORE]", NewSort(ia, FieldScore)}, {"[INT, STRING]", NewSort(ia, ib)}} {
		if _, err := cfg.SetIndexSort(c.s); err != nil {
			fmt.Printf("    %-18s -> 拒绝：%v\n", c.name, err)
		} else {
			fmt.Printf("    %-18s -> 接受，indexSortFields=%v\n", c.name, cfg.SortedFieldNames())
		}
	}

	fmt.Println("\n[4] GetPrimarySortField：段上真正生效的主排序字段")
	sab := NewSort(ia, ib)
	cases := []struct {
		label string
		r     *LeafReader
	}{
		{"正常", NewLeafReader(100, []string{"a", "b"}, sab, nil)},
		{"a 在本段无值", NewLeafReader(100, []string{"b"}, sab, nil)},
		{"a、b 都无值", NewLeafReader(100, nil, sab, nil)},
		{"a 全段同值", NewLeafReader(100, []string{"a", "b"}, sab,
			map[string]*Skipper{"a": {100, 7, 7}})},
		{"a 值有差异", NewLeafReader(100, []string{"a", "b"}, sab,
			map[string]*Skipper{"a": {100, 1, 9}})},
		{"skipper 只覆盖 50/100", NewLeafReader(100, []string{"a", "b"}, sab,
			map[string]*Skipper{"a": {50, 7, 7}})},
		{"两个字段都被跳过", NewLeafReader(100, []string{"a", "b"}, sab,
			map[string]*Skipper{"a": {100, 0, 0}, "b": {100, 3, 3}})},
		{"段无索引排序", NewLeafReader(100, []string{"a", "b"}, nil, nil)},
	}
	for _, c := range cases {
		p := GetPrimarySortField(c.r)
		name := "None"
		if p != nil {
			name = p.GetField()
			if name == "" {
				name = p.String()
			}
		}
		fmt.Printf("    %-22s -> %s\n", c.label, name)
	}

	fmt.Println("\n[5] IndexWriterConfig 默认值")
	fmt.Printf("    DEFAULT_MAX_FULL_FLUSH_MERGE_WAIT_MILLIS=%d\n",
		DefaultMaxFullFlushMergeWaitMillis)
	fmt.Printf("    DEFAULT_RAM_BUFFER_SIZE_MB=%.1f  DEFAULT_MAX_BUFFERED_DOCS=%d\n",
		DefaultRamBufferSizeMB, DefaultMaxBufferedDocs)
	c := NewIndexWriterConfig()
	before := c.FullFlushMergeEnabled()
	c.MaxFullFlushMergeWaitMillis = 0
	fmt.Printf("    full-flush 合并：默认=%v，设为 0 后=%v\n", before, c.FullFlushMergeEnabled())
}
