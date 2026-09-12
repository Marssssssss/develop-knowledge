package main

// cassandra_cl.go — Cassandra 一致性级别演示(Go 版核心机制)
//
// 演示:
//   1. CL 选择决策 — 8 个 CL 各自需 ack 数与 RF 的关系
//   2. 强一致性公式 W + R > RF
//   3. Hint 工作流(Cassandra 默认 max_hint_window_in_ms = 10800000 / 3h)
//
// 详解(含 read repair / anti-entropy / LWT)见 ../python/cassandra_cl.py

import "fmt"

type CL int

const (
	ANY CL = iota
	ONE
	TWO
	THREE
	QUORUM
	LOCAL_QUORUM
	EACH_QUORUM
	ALL
)

func (c CL) name() string {
	switch c {
	case ANY:
		return "ANY"
	case ONE:
		return "ONE"
	case TWO:
		return "TWO"
	case THREE:
		return "THREE"
	case QUORUM:
		return "QUORUM"
	case LOCAL_QUORUM:
		return "LOCAL_QUORUM"
	case EACH_QUORUM:
		return "EACH_QUORUM"
	case ALL:
		return "ALL"
	}
	return "?"
}

func requiredAcks(c CL, rf int) int {
	switch c {
	case ANY:
		return 1
	case ONE:
		return 1
	case TWO:
		return 2
	case THREE:
		return 3
	case QUORUM, LOCAL_QUORUM, EACH_QUORUM:
		return rf/2 + 1
	case ALL:
		return rf
	}
	return 0
}

func writeResult(c CL, alive, rf int) string {
	need := requiredAcks(c, rf)
	switch c {
	case ANY:
		if alive > 0 {
			return "OK"
		}
		return "FAIL (没活副本也没 hint)"
	case ALL:
		if alive >= rf {
			return "OK"
		}
		return fmt.Sprintf("FAIL (只有 %d 活,需要 %d)", alive, rf)
	default:
		if alive >= need {
			return "OK"
		}
		return fmt.Sprintf("FAIL (只有 %d 活,CL %s 需 %d)", alive, c.name(), need)
	}
}

func stronglyConsistent(W, R, rf int) bool {
	return W+R > rf
}

func main() {
	const RF = 3
	fmt.Printf("=== Cassandra 一致性级别演示 (Go 版核心机制,RF=%d) ===\n\n", RF)

	levels := []CL{ANY, ONE, TWO, THREE, QUORUM, LOCAL_QUORUM, EACH_QUORUM, ALL}

	scenarios := []struct {
		name  string
		alive int
	}{
		{"场景 A: 3 副本全 alive", 3},
		{"场景 B: 2 alive,1 down", 2},
		{"场景 C: 0 alive (3 down)", 0},
	}

	for _, s := range scenarios {
		fmt.Printf("--- %s ---\n", s.name)
		for _, cl := range levels {
			if cl == LOCAL_QUORUM || cl == EACH_QUORUM {
				continue
			}
			fmt.Printf("  CL=%-13s need=%d alive=%d → %s\n",
				cl.name(), requiredAcks(cl, RF), s.alive,
				writeResult(cl, s.alive, RF))
		}
		fmt.Println()
	}

	// 强一致性公式
	fmt.Println("--- 强一致性公式 W + R > RF ---")
	type combo struct {
		write, read, rf int
		name            string
	}
	combos := []combo{
		{2, 2, 3, "QUORUM/QUORUM"},
		{3, 1, 3, "ALL/ONE"},
		{1, 1, 3, "ONE/ONE"},
		{2, 1, 3, "QUORUM/ONE"},
	}
	for _, c := range combos {
		tag := "eventual"
		if stronglyConsistent(c.write, c.read, c.rf) {
			tag = "STRONG"
		}
		fmt.Printf("  %-14s : W+R=%d, RF=%d → %s\n",
			c.name, c.write+c.read, c.rf, tag)
	}

	fmt.Println("\n--- 关键参数(来源 cassandra.yaml) ---")
	fmt.Println("  hinted_handoff_enabled       : true (默认;窗口 3h)")
	fmt.Println("  max_hint_window_in_ms        : 10800000 (=3h)")
	fmt.Println("  read_repair_chance           : 0.1 (10% 读触发 blocking repair)")
	fmt.Println("  gc_grace_seconds             : 864000 (10 天,用于 tombstone purge)")
	fmt.Println()
	fmt.Println("工程推荐:多 DC 部署默认 LOCAL_QUORUM 而不是 QUORUM(避免跨 DC RTT)")
}
