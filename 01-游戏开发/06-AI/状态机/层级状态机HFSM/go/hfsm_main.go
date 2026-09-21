package main

import (
	"fmt"
)

// ---------------------------------------------------------------- 演示

func main() {
	m := newMachine()
	m.add(&state{id: scxml, kind: "compound", initial: []string{"s1"}})
	m.add(&state{id: "s1", parent: scxml, kind: "compound", initial: []string{"s1a"}})
	m.add(&state{id: "s1a", parent: "s1", kind: "atomic"})
	m.add(&state{id: "s1b", parent: "s1", kind: "atomic"})
	m.add(&state{id: "s2", parent: scxml, kind: "compound", initial: []string{"s2a"},
		history: &histDef{kind: "shallow", id: "h2", defTgt: []string{"s2a"}}})
	m.add(&state{id: "s2a", parent: "s2", kind: "atomic"})
	m.add(&state{id: "s2b", parent: "s2", kind: "atomic"})
	m.add(&state{id: "h2", parent: "s2", kind: "history"})
	m.states["h2"].trans = []*transition{{source: "h2", targets: []string{"s2a"}}}
	m.states["s1a"].trans = []*transition{{source: "s1a", event: "go", targets: []string{"s1b"}}}
	m.states["s1b"].trans = []*transition{{source: "s1b", event: "leave", targets: []string{"s2"}}}
	m.states["s2a"].trans = []*transition{{source: "s2a", event: "next", targets: []string{"s2b"}}}
	m.states["s2b"].trans = []*transition{{source: "s2b", event: "back", targets: []string{"h2"}}}

	m.start()
	fmt.Println("start   :", m.configList(), "entry:", m.entryLog)

	m.fire("go")
	fmt.Println("go      :", m.configList(), "exit:", m.exitLog[len(m.exitLog)-1:])

	m.exitLog = nil
	m.fire("leave")
	fmt.Println("leave   :", m.configList(), "exit:", m.exitLog)

	m.fire("next")
	fmt.Println("next    :", m.configList(), "history[h2]:", m.histVal["h2"])

	m.fire("back")
	fmt.Println("back    :", m.configList(), "(经 h2 回到 s2：shallow 历史记录的是 s2a)")
}

func keysOf(set map[string]bool) []string {
	out := []string{}
	for k := range set {
		out = append(out, k)
	}
	return out
}

func contains(list []string, v string) bool {
	for _, x := range list {
		if x == v {
			return true
		}
	}
	return false
}

func intersect(a, b []string) bool {
	for _, x := range a {
		if contains(b, x) {
			return true
		}
	}
	return false
}

func removeTrans(list []*transition, t *transition) []*transition {
	out := []*transition{}
	for _, x := range list {
		if x != t {
			out = append(out, x)
		}
	}
	return out
}
