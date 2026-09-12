package main

// MongoDB writeConcern / readConcern 决策器(Go 版核心机制)
//
// 用法: go run mongo_wc.go
//
// 详解(含 oplog 拉取 / 默认值公式 / 因果一致性)见 ../python/mongo_wc.py

import "fmt"

type WC struct {
	W           int    // 数值;负数表示 special("majority" 等)
	WLabel      string // "majority" / 描述
	J           bool
	WtimeoutMS  int
	HasWtimeout bool
}

func (w WC) describe() string {
	if w.W < 0 {
		return fmt.Sprintf("w=%s, j=%v, wtimeout=%s",
			w.WLabel, w.J, boolToStr(w.HasWtimeout))
	}
	return fmt.Sprintf("w=%d, j=%v, wtimeout=%s",
		w.W, w.J, boolToStr(w.HasWtimeout))
}

func boolToStr(b bool) string {
	if b {
		return "set"
	}
	return "unset"
}

func implicitDefaultWC(votingTotal, nArbiters int) string {
	nNonArbiters := votingTotal - nArbiters
	majority := votingTotal/2 + 1
	if nArbiters > 0 && nNonArbiters <= majority {
		return fmt.Sprintf("w=1 (P-S-A trap, voting=%d arbiters=%d)", votingTotal, nArbiters)
	}
	return fmt.Sprintf("w=majority (voting=%d arbiters=%d)", votingTotal, nArbiters)
}

type RC int

const (
	RCLocal RC = iota
	RCAvailable
	RCMajority
	RCLinearizable
	RCSnapshot
)

func (r RC) name() string {
	switch r {
	case RCLocal:
		return "local"
	case RCAvailable:
		return "available"
	case RCMajority:
		return "majority"
	case RCLinearizable:
		return "linearizable"
	case RCSnapshot:
		return "snapshot"
	}
	return "?"
}

func (r RC) summary() string {
	switch r {
	case RCLocal:
		return "节点最新值,可能回滚(默认)"
	case RCAvailable:
		return "分片最快,可能孤立文档"
	case RCMajority:
		return "已多数派持久化,不可回滚"
	case RCLinearizable:
		return "强一致读(仅 primary),会等并发写"
	case RCSnapshot:
		return "事务内一致性快照;须 w=majority"
	}
	return "?"
}

func main() {
	fmt.Println("=== MongoDB writeConcern & readConcern 决策器 (Go) ===\n")

	fmt.Println("--- 1. writeConcern 解析 ---")
	wcs := []WC{
		{W: 1, J: false},
		{W: 1, J: true},
		{W: 3, J: true, WtimeoutMS: 1000, HasWtimeout: true},
		{W: -1, WLabel: "majority", J: true, WtimeoutMS: 5000, HasWtimeout: true},
	}
	for _, wc := range wcs {
		fmt.Printf("  %s → %s\n", wc.name(), wc.describe())
	}

	fmt.Println("\n--- 2. 隐式默认 writeConcern ---")
	scenarios := []struct {
		Voting   int
		Arbiters int
		Desc     string
	}{
		{3, 0, "P-S-S"},
		{3, 1, "P-S-A"},
		{5, 1, "P-S-S-S-A"},
		{1, 0, "standalone"},
	}
	for _, s := range scenarios {
		fmt.Printf("  voting=%d arbiters=%d (%s) → %s\n",
			s.Voting, s.Arbiters, s.Desc,
			implicitDefaultWC(s.Voting, s.Arbiters))
	}

	fmt.Println("\n--- 3. readConcern 5 级 ---")
	for _, rc := range []RC{RCLocal, RCAvailable, RCMajority, RCLinearizable, RCSnapshot} {
		fmt.Printf("  %-13s : %s\n", rc.name(), rc.summary())
	}

	fmt.Println("\n--- 4. oplog & 复制路径 ---")
	fmt.Println("  - primary writes OpObserver hook → oplog.rs (capped collection)")
	fmt.Println("  - secondary tails oplog via tailable cursor")
	fmt.Println("  - commit point = min(replicatedTo across all members)")
	fmt.Println("  - readConcern 'majority' 要求节点 oplog 达到 commit point 才返回")

	fmt.Println("\n--- 5. 强一致 + 读己之写 ---")
	fmt.Println("  推荐组合:writeConcern={w:'majority', j:true} + readConcern='majority'")
	fmt.Println("  + 因果一致会话:驱动自动设置 afterClusterTime")
}
