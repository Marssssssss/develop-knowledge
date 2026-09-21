package main

import (
	"fmt"
	"strings"
)

// ---------------------------------------------------------------- 演示

func main() {
	var out []string
	report := func(label string, st NodeStatus, err error) {
		if err != nil {
			out = append(out, fmt.Sprintf("%-34s ERROR %v", label, err))
			return
		}
		out = append(out, fmt.Sprintf("%-34s %s", label, st))
	}

	// Sequence 的「记忆」：RUNNING 之后不再回头 tick 之前的孩子
	a := newLeaf("a", []NodeStatus{Success})
	b := newLeaf("b", []NodeStatus{Running})
	seq := newSequence("seq", a, b)
	st, _ := seq.ExecuteTick()
	report("sequence tick1", st, nil)
	st, _ = seq.ExecuteTick()
	report("sequence tick2", st, nil)
	out = append(out, fmt.Sprintf("%-34s a=%d b=%d", "  ticks(a,b)", a.ticks, b.ticks))

	// ReactiveSequence：每个 tick 都从头扫
	ra := newLeaf("ra", []NodeStatus{Success})
	rb := newLeaf("rb", []NodeStatus{Running})
	rs := newReactiveSequence("rs", ra, rb)
	rs.ExecuteTick()
	st, _ = rs.ExecuteTick()
	report("reactive tick2", st, nil)
	out = append(out, fmt.Sprintf("%-34s ra=%d rb=%d", "  ticks(ra,rb)", ra.ticks, rb.ticks))

	// Parallel 默认阈值：一个失败即整体失败，且剩余孩子不再 tick
	c0 := newLeaf("f", []NodeStatus{Failure})
	c1 := newLeaf("s1", []NodeStatus{Success})
	c2 := newLeaf("s2", []NodeStatus{Success})
	par := newParallel("par", -1, 1, c0, c1, c2)
	st, _ = par.ExecuteTick()
	report("parallel default threshold", st, nil)
	out = append(out, fmt.Sprintf("%-34s s1=%d s2=%d", "  ticks(s1,s2)", c1.ticks, c2.ticks))

	// Parallel 阈值不合法
	bad := newParallel("bad", 4, 1, newLeaf("x", []NodeStatus{Success}))
	_, err := bad.ExecuteTick()
	report("parallel too few children", Idle, err)

	// Inverter
	st, _ = newInverter("inv", newLeaf("s", []NodeStatus{Success})).ExecuteTick()
	report("inverter(SUCCESS)", st, nil)

	// Repeat
	rc := newLeaf("rc", []NodeStatus{Success})
	st, _ = newRepeat("rep", 3, rc).ExecuteTick()
	report("repeat N=3", st, nil)
	out = append(out, fmt.Sprintf("%-34s rc=%d", "  ticks(rc)", rc.ticks))

	fmt.Println(strings.Join(out, "\n"))
}
