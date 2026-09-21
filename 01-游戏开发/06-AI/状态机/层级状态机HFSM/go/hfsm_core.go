package main

// Package main 是 SCXML（W3C Recommendation）层级状态机语义的 Go 转写。
//
// 对应规范 https://www.w3.org/TR/scxml/ 的 Appendix D：
//   selectTransitions / removeConflictingTransitions / computeExitSet / exitStates
//   computeEntrySet / enterStates / getTransitionDomain / findLCCA /
//   addDescendantStatesToEnter / addAncestorStatesToEnter / getProperAncestors
//
// 语言差异显式落地：
//   - 规范的 OrderedSet → Go 用切片保序 + 判重（**顺序本身就是语义**，不能用 map 直接代替）；
//   - 规范的 isDescendant 只认「子 / 孙」，**不包含相等**，本实现同样严格；
//   - <scxml> 是容器不计入 configuration（见 README 注意事项）。

const scxml = "<scxml>"

type state struct {
	id       string
	parent   string
	kind     string // compound / parallel / atomic / final / history
	initial  []string
	children []string
	history  *histDef
	trans    []*transition
	order    int
}

type histDef struct {
	kind    string // shallow / deep
	id      string
	defTgt  []string
}

type transition struct {
	source  string
	event   string
	targets []string
	kind    string // external / internal
	order   int
}

func (s *state) isAtomic() bool {
	switch s.kind {
	case "atomic", "final":
		return true
	case "parallel", "history":
		return false
	}
	return len(s.children) == 0
}
func (s *state) isParallel() bool { return s.kind == "parallel" }
func (s *state) isCompound() bool { return s.kind == "compound" && !s.isAtomic() }

type machine struct {
	states   map[string]*state
	docOrder map[string]int
	config   map[string]bool
	histVal  map[string][]string
	exitLog  []string
	entryLog []string
}

func newMachine() *machine {
	return &machine{states: map[string]*state{}, docOrder: map[string]int{},
		config: map[string]bool{}, histVal: map[string][]string{}}
}

func (m *machine) add(s *state) {
	s.order = len(m.docOrder)
	m.states[s.id] = s
	m.docOrder[s.id] = s.order
	if s.parent != "" {
		if p, ok := m.states[s.parent]; ok {
			p.children = append(p.children, s.id)
		}
	}
}

func (m *machine) ancestors(id string) []string {
	out := []string{}
	cur := m.states[id].parent
	for cur != "" {
		out = append(out, cur)
		cur = m.states[cur].parent
	}
	return out
}

// properAncestors：规范原文「up to but not including state2」
func (m *machine) properAncestors(id, stop string) []string {
	out := []string{}
	for _, a := range m.ancestors(id) {
		if a == stop {
			break
		}
		out = append(out, a)
	}
	return out
}

func (m *machine) isDescendant(id, anc string) bool {
	for _, a := range m.ancestors(id) {
		if a == anc {
			return true
		}
	}
	return false
}

func (m *machine) inDocOrder(ids []string) []string {
	out := append([]string{}, ids...)
	for i := 1; i < len(out); i++ {
		for j := i; j > 0 && m.docOrder[out[j]] < m.docOrder[out[j-1]]; j-- {
			out[j], out[j-1] = out[j-1], out[j]
		}
	}
	return out
}

func (m *machine) entryOrder(ids []string) []string { return m.inDocOrder(ids) }

func (m *machine) exitOrder(ids []string) []string {
	sorted := m.inDocOrder(ids)
	out := []string{}
	for i := len(sorted) - 1; i >= 0; i-- {
		out = append(out, sorted[i])
	}
	return out
}

func (m *machine) atomicStates() []string {
	out := []string{}
	for id := range m.config {
		if m.states[id].isAtomic() {
			out = append(out, id)
		}
	}
	return m.inDocOrder(out)
}
