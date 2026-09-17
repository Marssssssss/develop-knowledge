// cassandra_model_check.go — 自检:主键解析、分区规模、墓碑清除条件、压缩策略
//
// 与 cassandra_model.go 同属 package main。断言逐条对应 apache/cassandra
// 文档源文件中的明文规则(出处见 cassandra_model.go 文件头)。
//
// 运行: go run cassandra_model.go cassandra_model_check.go
package main

import (
	"fmt"
	"os"
	"strings"
)

func main() {
	fmt.Println("[1] 主键解析:第一个分量生成分区键(官方原文)")
	pk, ck := ParsePrimaryKey("id")
	check("PRIMARY KEY (id) → [id] / []", eqStrs(pk, []string{"id"}) && len(ck) == 0)
	pk, ck = ParsePrimaryKey("id,c")
	check("PRIMARY KEY (id,c) → [id] / [c]", eqStrs(pk, []string{"id"}) && eqStrs(ck, []string{"c"}))
	pk, ck = ParsePrimaryKey("(id1,id2),c1,c2")
	check("PRIMARY KEY ((id1,id2),c1,c2) → 复合分区键 + 两个聚簇键",
		eqStrs(pk, []string{"id1", "id2"}) && eqStrs(ck, []string{"c1", "c2"}), fmt.Sprint(pk, ck))
	pk, ck = ParsePrimaryKey("( id1 , id2 ), c1")
	check("括号内空白被规范化", eqStrs(pk, []string{"id1", "id2"}) && eqStrs(ck, []string{"c1"}))

	fmt.Println("[2] 分区定位与分区规模")
	s := Schema{Name: "magazine_publisher", PartitionKey: []string{"publisher"}, Clustering: []string{"id"}}
	check("分区键等值绑定 → 单分区", !s.NeedsFiltering([]string{"publisher"}))
	check("只绑聚簇键 → 需过滤全部", s.NeedsFiltering([]string{"id"}))
	check("复合分区键只绑一半 → 需过滤", Schema{PartitionKey: []string{"id1", "id2"}}.NeedsFiltering([]string{"id1"}))
	check("值个数超 10 万 → 告警",
		len(PartitionWarnings(MaxPartitionValues+1, 0)) == 1)
	check("磁盘超 100 MB → 告警",
		len(PartitionWarnings(0, MaxPartitionBytes+1)) == 1)
	check("恰好达线不告警", len(PartitionWarnings(MaxPartitionValues, MaxPartitionBytes)) == 0)

	fmt.Println("[3] 读路径:墓碑遮挡更早时间戳(官方原文)")
	rows := []Row{{PK: "P1", CK: "C1", Cell: Cell{Ts: 50, Value: "v1"}},
		{PK: "P1", CK: "C1", Cell: Cell{Ts: 100, Tombstone: true}}}
	_, visible := Read(rows, "P1", "C1")
	check("墓碑遮住更早的 v1 → 读到空", !visible)
	rows2 := append(rows, Row{PK: "P1", CK: "C1", Cell: Cell{Ts: 150, Value: "v2"}})
	v, ok := Read(rows2, "P1", "C1")
	check("宽限期内新写 v2 胜过墓碑 → 读到 v2", ok && v == "v2", v)
	_, otherVisible := Read(append(rows, Row{PK: "P1", CK: "C2", Cell: Cell{Ts: 10, Value: "o"}}), "P1", "C2")
	check("墓碑不影响同分区的其他聚簇键", otherVisible)

	fmt.Println("[4] 墓碑清除的三个条件(官方原文)")
	tomb := Cell{Ts: 1000, Tombstone: true, DeletedAt: 1000}
	old := Sstable{Name: "old", Rows: []Row{{PK: "P1", CK: "C1", Cell: Cell{Ts: 500, Value: "v1"}}}}
	other := Sstable{Name: "other", Rows: []Row{{PK: "P2", CK: "C1", Cell: Cell{Ts: 500, Value: "x"}}}}
	check("默认宽限期 864000 秒(10 天)", DefaultGCGraceSeconds == 10*86400)
	ok1, why1 := Purgeable(tomb, 1000+DefaultGCGraceSeconds-1, "P1", []Sstable{old},
		[]Sstable{old}, true, false, DefaultGCGraceSeconds)
	check("宽限期内不可清除", !ok1 && why1 == "within_grace_period", why1)
	ok2, why2 := Purgeable(tomb, 1000+DefaultGCGraceSeconds+1, "P1", []Sstable{old},
		[]Sstable{old}, true, false, DefaultGCGraceSeconds)
	check("过期 + 遮蔽文件同在本次压缩 → 可清除", ok2 && why2 == "purgeable", why2)
	ok3, why3 := Purgeable(tomb, 1000+DefaultGCGraceSeconds+1, "P1", []Sstable{},
		[]Sstable{old, other}, true, false, DefaultGCGraceSeconds)
	check("过期但遮蔽文件不在本次压缩 → 不可清除",
		!ok3 && strings.HasPrefix(why3, "shadowing_sstable_outside_compaction"), why3)
	ok4, why4 := Purgeable(tomb, 1000+DefaultGCGraceSeconds+1, "P1", []Sstable{old},
		[]Sstable{old}, false, true, DefaultGCGraceSeconds)
	check("only_purge_repaired_tombstones 且未修复 → 不可清除",
		!ok4 && why4 == "only_purge_repaired_tombstones", why4)
	ok5, _ := Purgeable(tomb, 1000+DefaultGCGraceSeconds, "P1", []Sstable{old},
		[]Sstable{old}, true, false, DefaultGCGraceSeconds)
	check("边界:恰好等于 gc_grace 仍算宽限期内", !ok5)

	fmt.Println("[5] 完全过期的 SSTable")
	expired := Sstable{Name: "expired", Rows: []Row{{PK: "P3", CK: "C1", Cell: Cell{Ts: 10, Value: "v", TTL: 1}}}}
	live := Sstable{Name: "live", Rows: []Row{{PK: "P3", CK: "C1", Cell: Cell{Ts: 10, Value: "v", TTL: 100}}}}
	check("只剩过期 TTL 的文件 → fully expired",
		expired.ExpiredOnly(1000000) && !live.ExpiredOnly(50))
	check("含无 TTL 数据的文件永不算 fully expired",
		!Sstable{Name: "x", Rows: []Row{{PK: "P3", CK: "C1", Cell: Cell{Ts: 10, Value: "v"}}}}.ExpiredOnly(1<<40))

	fmt.Println("[6] 压缩策略")
	check("STCS 默认阈值 4", StcsMinThreshold == 4)
	check("4 个大小相近(100/102/98/101)→ 触发", len(StcsTrigger([]int{100, 102, 98, 101}, StcsMinThreshold)) == 4)
	check("3 个不够触发", StcsTrigger([]int{100, 100, 100}, StcsMinThreshold) == nil)
	check("4 个但大小悬殊 → 不落同一桶", StcsTrigger([]int{1, 100, 200, 4000}, StcsMinThreshold) == nil)
	targets := LcsTargets(4, 160)
	check("LCS 每层目标为上一层 10 倍(官方原文)",
		fmt.Sprint(targets) == "[160 1600 16000 160000]", fmt.Sprint(targets))
	check("L0 超过 32 个 → 先在 L0 走 STCS(官方原文)",
		L0Failsafe(33) == "stcs_in_l0" && L0Failsafe(32) == "lcs")
	w := TwcsWindows([]int{0, 10, 60, 61, 130}, 60)
	check("TWCS 按 60 秒窗口分桶", len(w) == 3 && len(w[1]) == 2, fmt.Sprint(w))

	fmt.Println("")
	fmt.Printf("断言总数 %d,失败 %d\n", pass+fail, fail)
	if fail > 0 {
		os.Exit(1)
	}
	fmt.Println("全部通过")
}
