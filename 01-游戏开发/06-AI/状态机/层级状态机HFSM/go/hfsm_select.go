package main

// ---------------------------------------------------------------- 转移选择

func (m *machine) selectTransitions(event string) []*transition {
	enabled := []*transition{}
	for _, st := range m.atomicStates() {
		chain := append([]string{st}, m.properAncestors(st, "")...)
		for _, sid := range chain {
			var found *transition
			best := -1
			for _, t := range m.states[sid].trans {
				if t.event == event && (best < 0 || t.order < best) {
					found, best = t, t.order
				}
			}
			if found != nil {
				enabled = append(enabled, found)
				break
			}
		}
	}
	return m.removeConflicting(enabled)
}

func (m *machine) computeExitSet(transitions []*transition) []string {
	out := []string{}
	for _, t := range transitions {
		if len(t.targets) == 0 {
			continue // targetless
		}
		domain := m.getTransitionDomain(t)
		for s := range m.config {
			if m.isDescendant(s, domain) && !contains(out, s) {
				out = append(out, s)
			}
		}
	}
	return out
}

func (m *machine) removeConflicting(enabled []*transition) []*transition {
	filtered := []*transition{}
	for _, t1 := range enabled {
		preempted := false
		toRemove := []*transition{}
		for _, t2 := range filtered {
			if intersect(m.computeExitSet([]*transition{t1}), m.computeExitSet([]*transition{t2})) {
				if m.isDescendant(t1.source, t2.source) {
					toRemove = append(toRemove, t2)
				} else {
					preempted = true
					break
				}
			}
		}
		if !preempted {
			for _, t3 := range toRemove {
				filtered = removeTrans(filtered, t3)
			}
			filtered = append(filtered, t1)
		}
	}
	return filtered
}
