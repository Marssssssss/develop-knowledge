package main

// ---------------------------------------------------------------- 动作 / 目标 / 节点

type action struct {
	name          string
	preconditions *state
	effects       *state
	cost          float64
}

type goal struct {
	name       string
	goalState  *state
	priority   float64
	plan       []string
}

type planner struct {
	world   *state
	actions []*action
	// 默认值取自 ReGoapPlannerSettings
	planningEarlyExit bool
	maxIterations     int
	maxNodesToExpand  int
	enqueued          int
	iterations        int
}

func newPlanner(world map[string]any, actions []*action) *planner {
	return &planner{world: newState(world), actions: actions,
		planningEarlyExit: false, maxIterations: 1000, maxNodesToExpand: 10000}
}

type node struct {
	pl   *planner
	act  *action
	parent *node
	state  *state
	goal   *state
	g      float64
	h      float64
	cost   float64
	merged int
}

func newNode(pl *planner, g *state, parent *node, act *action) *node {
	n := &node{pl: pl, act: act, parent: parent}
	if parent != nil {
		n.state = parent.state.clone()
		n.g = parent.g
	} else {
		n.state = pl.world.clone()
		n.g = 0
	}
	if act != nil {
		n.goal = g.clone()
		n.g += act.cost
		n.state.addFrom(act.effects)
		n.goal.replaceWithMissingDifference(act.effects)
		n.goal.addFrom(act.preconditions)
	} else {
		n.goal = g
	}
	n.h = float64(n.goal.count())
	n.cost = n.g + n.h
	merged := newState(nil)
	n.merged = n.goal.missingDifference(pl.world, merged)
	return n
}

func (n *node) isGoal() bool { return n.merged <= 0 }

func (n *node) expand() []*node {
	out := []*node{}
	for i := len(n.pl.actions) - 1; i >= 0; i-- { // 官方倒序遍历动作表
		cand := n.pl.actions[i]
		if cand.effects.hasAny(n.goal) &&
			!n.goal.hasAnyConflictRelaxed(cand.effects, cand.preconditions) &&
			!n.goal.hasAnyConflict(cand.effects) {
			out = append(out, newNode(n.pl, n.goal, n, cand))
		}
	}
	return out
}

func (n *node) calculatePath() []string {
	res := []string{}
	for cur := n; cur.parent != nil; cur = cur.parent {
		res = append(res, cur.act.name)
	}
	return res // 反向规划 ⇒ 回溯即正序
}

// ---------------------------------------------------------------- A*

func (pl *planner) astar(start *node) *node {
	frontier := []*node{start}
	stateToNode := map[string]*node{}
	explored := map[string]*node{}
	pl.iterations = 0

	for len(frontier) > 0 && pl.iterations < pl.maxIterations && len(frontier)+1 < pl.maxNodesToExpand {
		// 线性取最小 f（Go 无优先队列，规模小足够）
		best := 0
		for i := 1; i < len(frontier); i++ {
			if frontier[i].cost < frontier[best].cost {
				best = i
			}
		}
		cur := frontier[best]
		frontier = append(frontier[:best], frontier[best+1:]...)

		if cur.isGoal() {
			return cur
		}
		explored[cur.state.key()] = cur

		for _, child := range cur.expand() {
			pl.iterations++
			if pl.planningEarlyExit && child.isGoal() {
				return child
			}
			key := child.state.key()
			if _, ok := explored[key]; ok {
				continue
			}
			if similar, ok := stateToNode[key]; ok {
				if similar.cost > child.cost {
					delete(stateToNode, key)
					frontier = removeNode(frontier, similar)
				} else {
					break // 官方是 break 不是 continue
				}
			}
			frontier = append(frontier, child)
			stateToNode[key] = child
			pl.enqueued++
		}
	}
	return nil
}

func removeNode(list []*node, target *node) []*node {
	out := list[:0]
	for _, n := range list {
		if n != target {
			out = append(out, n)
		}
	}
	return out
}
