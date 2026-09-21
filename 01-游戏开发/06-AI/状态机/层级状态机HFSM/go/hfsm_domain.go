package main

// ---------------------------------------------------------------- 域与 LCCA

func (m *machine) effectiveTargets(t *transition) []string {
	out := []string{}
	for _, s := range t.targets {
		st := m.states[s]
		if st.kind == "history" {
			if vals, ok := m.histVal[s]; ok && len(vals) > 0 {
				for _, x := range vals {
					if !contains(out, x) {
						out = append(out, x)
					}
				}
				continue
			}
			for _, x := range m.effectiveTargets(st.trans[0]) {
				if !contains(out, x) {
					out = append(out, x)
				}
			}
			continue
		}
		if !contains(out, s) {
			out = append(out, s)
		}
	}
	return out
}

func (m *machine) getTransitionDomain(t *transition) string {
	targets := m.effectiveTargets(t)
	if len(targets) == 0 {
		return ""
	}
	src := m.states[t.source]
	if t.kind == "internal" && src.isCompound() {
		all := true
		for _, x := range targets {
			if !m.isDescendant(x, t.source) {
				all = false
			}
		}
		if all {
			return t.source
		}
	}
	return m.findLCCA(append([]string{t.source}, targets...))
}

func (m *machine) findLCCA(list []string) string {
	best, bestDepth := scxml, -1
	for id := range m.docOrder {
		if id == scxml || contains(list, id) {
			continue // 必须是 proper ancestor
		}
		all := true
		for _, x := range list {
			if !m.isDescendant(x, id) {
				all = false
			}
		}
		if all && len(m.ancestors(id)) > bestDepth {
			best, bestDepth = id, len(m.ancestors(id))
		}
	}
	return best
}
