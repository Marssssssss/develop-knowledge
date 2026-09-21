package main

import (
	"fmt"
)

// ---------------------------------------------------------------- 演示

func main() {
	actions := []*action{
		{"GetAxe", newState(nil), newState(map[string]any{"hasAxe": true}), 1},
		{"ChopWood", newState(map[string]any{"hasAxe": true}), newState(map[string]any{"hasWood": true}), 2},
		{"BuyWood", newState(nil), newState(map[string]any{"hasWood": true}), 5},
	}
	pl := newPlanner(map[string]any{}, actions)
	g := &goal{name: "haveWood", goalState: newState(map[string]any{"hasWood": true}), priority: 1}
	got := pl.plan([]*goal{g})
	fmt.Printf("plan        = %v (iterations=%d, enqueued=%d)\n", got.plan, pl.iterations, pl.enqueued)

	pl2 := newPlanner(map[string]any{}, actions)
	pl2.planningEarlyExit = true
	g2 := &goal{name: "haveWood", goalState: newState(map[string]any{"hasWood": true}), priority: 1}
	got2 := pl2.plan([]*goal{g2})
	fmt.Printf("early-exit  = %v (iterations=%d)\n", got2.plan, pl2.iterations)

	// 条件对象作为目标值：hasOre >= 2
	pl3 := newPlanner(map[string]any{}, []*action{
		{"Mine", newState(nil), newState(map[string]any{"ore": 3}), 1},
	})
	g3 := &goal{name: "ore", goalState: newState(map[string]any{"ore": ge(2)}), priority: 1}
	got3 := pl3.plan([]*goal{g3})
	fmt.Printf("cond goal   = %v (isMatch(>=2, 3)=%v)\n", got3.plan, isMatch(ge(2), 3))
}
