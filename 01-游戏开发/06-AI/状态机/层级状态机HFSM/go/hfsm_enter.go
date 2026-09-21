package main

import (
	"strings"
)

// ---------------------------------------------------------------- 进入 / 退出

func (m *machine) addDescendantStatesToEnter(id string, toEnter *[]string, forDefault map[string]bool, hist map[string][]string) {
	st := m.states[id]
	if st.kind == "history" {
		values := m.histVal[id]
		if len(values) > 0 {
			for _, x := range values {
				m.addDescendantStatesToEnter(x, toEnter, forDefault, hist)
			}
			for _, x := range values {
				m.addAncestorStatesToEnter(x, st.parent, toEnter, forDefault, hist)
			}
		} else {
			hist[st.parent] = st.trans[0].targets
			for _, x := range st.trans[0].targets {
				m.addDescendantStatesToEnter(x, toEnter, forDefault, hist)
				m.addAncestorStatesToEnter(x, st.parent, toEnter, forDefault, hist)
			}
		}
		return
	}
	if !contains(*toEnter, id) {
		*toEnter = append(*toEnter, id)
	}
	if st.isParallel() {
		for _, child := range st.children {
			found := false
			for _, x := range *toEnter {
				if m.isDescendant(x, child) {
					found = true
				}
			}
			if !found {
				m.addDescendantStatesToEnter(child, toEnter, forDefault, hist)
			}
		}
	} else if st.isCompound() {
		forDefault[id] = true
		for _, child := range st.initial {
			m.addDescendantStatesToEnter(child, toEnter, forDefault, hist)
		}
		for _, child := range st.initial {
			m.addAncestorStatesToEnter(child, id, toEnter, forDefault, hist)
		}
	}
}

func (m *machine) addAncestorStatesToEnter(id, ancestor string, toEnter *[]string, forDefault map[string]bool, hist map[string][]string) {
	for _, anc := range m.properAncestors(id, ancestor) {
		if !contains(*toEnter, anc) {
			*toEnter = append(*toEnter, anc)
		}
		if m.states[anc].isParallel() {
			for _, child := range m.states[anc].children {
				found := false
				for _, x := range *toEnter {
					if m.isDescendant(x, child) {
						found = true
					}
				}
				if !found {
					m.addDescendantStatesToEnter(child, toEnter, forDefault, hist)
				}
			}
		}
	}
}

func (m *machine) enter(toEnter []string) {
	for _, s := range m.entryOrder(toEnter) {
		if s == scxml {
			continue
		}
		m.config[s] = true
		m.entryLog = append(m.entryLog, s)
	}
}

func (m *machine) exit(statesToExit []string) {
	ordered := m.exitOrder(statesToExit)
	// 规范：先统一记录历史值，再逐个退出
	for _, s := range ordered {
		st := m.states[s]
		if st.history == nil {
			continue
		}
		value := []string{}
		for x := range m.config {
			if st.history.kind == "deep" {
				if m.states[x].isAtomic() && m.isDescendant(x, s) {
					value = append(value, x)
				}
			} else if m.states[x].parent == s {
				value = append(value, x)
			}
		}
		m.histVal[st.history.id] = value
	}
	for _, s := range ordered {
		m.exitLog = append(m.exitLog, s)
		delete(m.config, s)
	}
}

func (m *machine) fire(event string) bool {
	enabled := m.selectTransitions(event)
	if len(enabled) == 0 {
		return false
	}
	m.exit(m.computeExitSet(enabled))
	toEnter := []string{}
	forDefault := map[string]bool{}
	hist := map[string][]string{}
	for _, t := range enabled {
		for _, s := range t.targets {
			m.addDescendantStatesToEnter(s, &toEnter, forDefault, hist)
		}
		ancestor := m.getTransitionDomain(t)
		for _, s := range m.effectiveTargets(t) {
			m.addAncestorStatesToEnter(s, ancestor, &toEnter, forDefault, hist)
		}
	}
	m.enter(toEnter)
	return true
}

func (m *machine) start() {
	toEnter := []string{}
	forDefault := map[string]bool{}
	hist := map[string][]string{}
	for _, s := range m.states[scxml].initial {
		m.addDescendantStatesToEnter(s, &toEnter, forDefault, hist)
	}
	m.enter(toEnter)
}

func (m *machine) configList() string {
	return "{" + strings.Join(m.inDocOrder(keysOf(m.config)), ", ") + "}"
}
