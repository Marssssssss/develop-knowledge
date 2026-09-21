package main

import (
	"sort"
)

// ---------------------------------------------------------------- 目标选择与规划

func (pl *planner) selectNextGoal(goals []*goal, roll float64, weighted bool) *goal {
	sorted := append([]*goal{}, goals...)
	sort.SliceStable(sorted, func(i, j int) bool { return sorted[i].priority < sorted[j].priority })
	if !weighted || len(sorted) == 1 {
		return sorted[len(sorted)-1]
	}
	total := 0.0
	for _, g := range sorted {
		w := g.priority
		if w < 0 {
			w = 0
		}
		if w < 0.001 {
			w = 0.001
		}
		total += w
	}
	cumulative := 0.0
	target := roll * total
	for _, g := range sorted {
		w := g.priority
		if w < 0 {
			w = 0
		}
		if w < 0.001 {
			w = 0.001
		}
		cumulative += w
		if target <= cumulative {
			return g
		}
	}
	return sorted[len(sorted)-1]
}

func (pl *planner) plan(goals []*goal) *goal {
	possible := append([]*goal{}, goals...)
	sort.SliceStable(possible, func(i, j int) bool { return possible[i].priority < possible[j].priority })
	for len(possible) > 0 {
		current := pl.selectNextGoal(possible, 0, false)
		rest := []*goal{}
		for _, g := range possible {
			if g != current {
				rest = append(rest, g)
			}
		}
		possible = rest
		leaf := pl.astar(newNode(pl, current.goalState, nil, nil))
		if leaf == nil {
			continue
		}
		path := leaf.calculatePath()
		if len(path) == 0 {
			continue // 目标已被满足 ⇒ 空计划，官方视为没有计划
		}
		current.plan = path
		return current
	}
	return nil
}
