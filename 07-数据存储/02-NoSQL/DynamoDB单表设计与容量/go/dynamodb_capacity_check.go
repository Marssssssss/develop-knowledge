// dynamodb_capacity_check.go — 自检:容量换算、LSI/GSI 语义、项目集合与分区硬顶
//
// 与 dynamodb_capacity.go 同属 package main。标注「官方示例」的断言直接使用 AWS
// 文档给出的数字。
//
// 运行: go run dynamodb_capacity.go dynamodb_capacity_check.go
package main

import (
	"fmt"
	"os"
)

// ------------------------------------------------------------------ 自检

var pass, fail int

func check(label string, cond bool, detail ...string) {
	if cond {
		pass++
		fmt.Println("  PASS ", label)
		return
	}
	fail++
	extra := ""
	if len(detail) > 0 {
		extra = detail[0]
	}
	fmt.Println("  FAIL ", label, extra)
}

func main() {
	fmt.Println("[1] 读/写单位(官方示例数字)")
	u1, _ := ReadUnits(10*kb, "eventual")
	check("10 KB 最终一致读 = 1.5 读单位(官方示例)", u1 == 1.5, fmt.Sprint(u1))
	u2, _ := ReadUnits(10*kb, "strong")
	check("10 KB 强一致读 = 3 读单位(官方示例)", u2 == 3, fmt.Sprint(u2))
	u3, _ := ReadUnits(10*kb, "transactional")
	check("10 KB 事务读 = 6 读单位(官方示例)", u3 == 6, fmt.Sprint(u3))
	check("3 KB 标准写 = 3 写单位(官方示例)", WriteUnits(3*kb, false) == 3)
	check("3 KB 事务写 = 6 写单位(官方示例)", WriteUnits(3*kb, true) == 6)
	check("10 KB 事务写 = 20 写单位(官方示例)", WriteUnits(10*kb, true) == 20)

	fmt.Println("[2] 单分区硬顶 3000/1000(官方原文)")
	u20, _ := ReadUnits(20*kb, "strong")
	check("20 KB 条目强一致读 = 5 读单位(官方示例)", u20 == 5, fmt.Sprint(u20))
	check("该条目单分区 = 600 次/秒(官方示例)", float64(perPartitionRCU)/u20 == 600)
	check("每分区 1000 写/秒:1000 不越界、1001 越界",
		!HotPartition(1000, perPartitionWCU) && HotPartition(1001, perPartitionWCU))

	fmt.Println("[3] BatchGetItem 逐条取整 vs Query 整体取整(官方示例)")
	check("1.5 KB + 6.5 KB → 12 KB → 3 读单位(官方示例)",
		BatchGetUnits([]int{1536, 6656}, "strong") == 3, fmt.Sprint(BatchGetUnits([]int{1536, 6656}, "strong")))
	check("同样的两条走 Query → 8 KB → 2 读单位", QueryUnits(1536+6656, "strong") == 2)
	check("Query 合计 40.8 KB → 44 KB → 11 读单位(官方示例)",
		QueryUnits(41779, "strong") == 11, fmt.Sprint(QueryUnits(41779, "strong")))

	fmt.Println("[4] 条目与键长度上限")
	check("400 KB 合法、400 KB+1 非法",
		ValidateItemSize(maxItemBytes) == "" && ValidateItemSize(maxItemBytes+1) == "ItemSizeTooLarge")
	check("分区键 2048 合法、0 与 2049 非法",
		len(ValidateKeys(2048, 0)) == 0 && len(ValidateKeys(0, 0)) == 1 && len(ValidateKeys(2049, 0)) == 1)
	check("排序键 1024 合法、1025 非法",
		len(ValidateKeys(10, 1024)) == 0 && len(ValidateKeys(10, 1025)) == 1)
	check("每页 1 MB:2.5 MB → 3 页", ceilDiv(int(2.5*float64(mb)), maxPageBytes) == 3)

	fmt.Println("[5] 写分片(官方示例后缀 1..200)")
	check("1000 WCU 不分片、1001 WCU 分 2 片", ShardCount(1000) == 1 && ShardCount(1001) == 2)
	check("200000 WCU → 200 个分片(与官方示例一致)", ShardCount(200000) == 200, fmt.Sprint(ShardCount(200000)))

	fmt.Println("[6] LSI / GSI 结构约束")
	t := NewTable()
	check("LSI 建表时创建成功", t.AddLSI(Index{Name: "by_ts", Kind: "LSI", SkAttr: "created_at"}, true) == nil)
	check("LSI 事后追加 → 报错", t.AddLSI(Index{Name: "late", Kind: "LSI", SkAttr: "x"}, false) != nil)
	check("LSI 带自有吞吐 → 报错(与主表共用吞吐)",
		NewTable().AddLSI(Index{Name: "bad", Kind: "LSI", SkAttr: "x", WCU: 10}, true) != nil)
	check("GSI 缺自有吞吐 → 报错", NewTable().AddGSI(Index{Name: "g", Kind: "GSI", SkAttr: "x"}) != nil)
	g := NewTable()
	for i := 0; i < maxGSI; i++ {
		g.AddGSI(Index{Name: fmt.Sprintf("g%d", i), Kind: "GSI", SkAttr: "x", WCU: 10})
	}
	check("GSI 默认配额 20 个", len(g.gsis) == maxGSI && g.AddGSI(Index{Name: "g21", Kind: "GSI", SkAttr: "x", WCU: 10}) != nil)

	fmt.Println("[7] GSI 写入语义")
	t6 := NewTable()
	t6.AddGSI(Index{Name: "ByTitle", Kind: "GSI", SkAttr: "TopScore", Projection: "ALL", WCU: 5})
	_, iu1, e1 := t6.Put("USER#1", "GAME#A", map[string]string{"TopScore": "0"}, 200)
	_, iu2, e2 := t6.Put("USER#2", "GAME#A", map[string]string{"TopScore": "0"}, 200)
	check("GSI 键值不必唯一:两条同键写入均成功",
		e1 == nil && e2 == nil && iu1["ByTitle"] == 1 && iu2["ByTitle"] == 1)
	_, iu3, e3 := t6.Put("USER#3", "GAME#B", map[string]string{}, 200)
	check("缺 GSI 排序键 → 不写索引条目、不耗 GSI 容量(官方原文)",
		e3 == nil && len(iu3) == 0 && iu3["ByTitle"] == 0)
	t7 := NewTable()
	t7.AddGSI(Index{Name: "Tiny", Kind: "GSI", SkAttr: "gsisk", Projection: "ALL", WCU: 1})
	_, _, e7 := t7.Put("P#1", "S#1", map[string]string{"gsisk": "v"}, 2*kb)
	check("GSI 写容量不足 → 主表写被限流(官方原文)", e7 == errThrottle)

	fmt.Println("[8] 读一致性与项目集合")
	t8 := NewTable()
	check("GSI 强一致读 → 报错", t8.CheckRead("GSI", true) != nil)
	check("LSI 强一致读 → 合法", t8.CheckRead("LSI", true) == nil)
	big := NewTable()
	big.CollectionLimit = 2500
	big.AddLSI(Index{Name: "Lall", Kind: "LSI", SkAttr: "created_at", Projection: "ALL"}, true)
	big.Put("P#1", "S#1", map[string]string{"created_at": "a"}, 1000)
	check("项目集合 = 主表条目 + LSI 条目(官方原文)", big.CollectionBytes("P#1") == 2000,
		fmt.Sprint(big.CollectionBytes("P#1")))
	_, _, e8 := big.Put("P#1", "S#2", map[string]string{"created_at": "b"}, 1000)
	check("增长型写越上限 → ItemCollectionSizeLimitExceededException", e8 == errCollection)
	_, _, e9 := big.Put("P#1", "S#1", map[string]string{"created_at": "a"}, 500)
	check("缩小集合的写仍允许(官方原文)", e9 == nil)
	check("无 LSI 的表不受该上限约束", NewTable().CollectionBytes("P#1") == 0)

	fmt.Println("[9] 投影属性配额与条目大小")
	check("投影属性合计上限 100",
		ProjectedAttrCount([]Index{{Projection: "INCLUDE", Included: []string{"p", "q"}},
			{Projection: "INCLUDE", Included: []string{"p"}}}) == 3)
	check("KEYS_ONLY / ALL 不计入配额",
		ProjectedAttrCount([]Index{{Projection: "ALL"}, {Projection: "KEYS_ONLY"}}) == 0)
	it := map[string]string{"a": "xxxxxxxxxx"}
	check("ALL 投影条目与主表等大",
		(Index{Projection: "ALL"}).EntrySize("P#1", "S#1", it, 500) == 500)
	check("KEYS_ONLY 条目远小于主表条目",
		(Index{Projection: "KEYS_ONLY"}).EntrySize("P#1", "S#1", it, 500) < 100)

	fmt.Println("")
	fmt.Printf("断言总数 %d,失败 %d\n", pass+fail, fail)
	if fail > 0 {
		os.Exit(1)
	}
	fmt.Println("全部通过")
}
