package main

import (
	"fmt"
	"sort"
)

// ---------------------------------------------------------------- 前置条件

type rule struct {
	r       string
	allowed Set
}

// InferPrerequisites 论文式 (2)：.* r (a1|..|aj)*
func InferPrerequisites(sessions [][]string, types []string) map[string][]rule {
	out := map[string][]rule{}
	for _, m := range types {
		out[m] = nil
	}
	for _, r := range types {
		var mR []string
		aR := map[string]bool{}
		for _, m := range types {
			if m == r {
				continue
			}
			okAll := true
			for _, s := range sessions {
				first := -1
				for i, t := range s {
					if t == m {
						first = i
						break
					}
				}
				if first < 0 {
					continue
				}
				hasR := false
				for j := 0; j < first; j++ {
					if s[j] == r {
						hasR = true
					}
				}
				if !hasR {
					okAll = false
					break
				}
			}
			if !okAll {
				continue
			}
			for _, s := range sessions {
				last := -1
				for i, t := range s {
					if t == r {
						last = i
					} else if t == m && last >= 0 {
						for _, x := range s[last+1 : i] {
							aR[x] = true
						}
					}
				}
			}
			mR = append(mR, m)
		}
		if len(mR) > 0 {
			allow := newSet()
			for k := range aR {
				allow = append(allow, k)
			}
			sort.Strings(allow)
			allow = newSet(allow...)
			for _, m := range mR {
				out[m] = append(out[m], rule{r, allow})
			}
		}
	}
	return out
}

// Matches 路径是否满足 .* r (A)*
func Matches(path []string, ru rule) bool {
	for j := len(path) - 1; j >= 0; j-- {
		if path[j] != ru.r {
			continue
		}
		good := true
		for _, t := range path[j+1:] {
			if !ru.allowed.has(t) {
				good = false
				break
			}
		}
		if good {
			return true
		}
	}
	return false
}

// EndTypes 在所有会话中只出现在末位的消息类型。
func EndTypes(sessions [][]string, types []string) Set {
	out := Set{}
	for _, m := range types {
		seen, onlyLast := false, true
		for _, s := range sessions {
			for i, t := range s {
				if t != m {
					continue
				}
				seen = true
				if i != len(s)-1 {
					onlyLast = false
				}
			}
		}
		if seen && onlyLast {
			out = append(out, m)
		}
	}
	sort.Strings(out)
	return out
}

// LabelStates 给每个状态标上允许的消息类型集合。
func LabelStates(t *APTA, prereq map[string][]rule, ends Set) map[string]Set {
	out := map[string]Set{}
	for _, st := range t.states() {
		p := pathOf(st)
		if len(p) > 0 && ends.has(p[len(p)-1]) {
			out[st] = Set{}
			continue
		}
		allow := Set{}
		for _, m := range t.types() {
			all := true
			for _, ru := range prereq[m] {
				if !Matches(p, ru) {
					all = false
					break
				}
			}
			if all {
				allow = append(allow, m)
			}
		}
		sort.Strings(allow)
		out[st] = allow
	}
	return out
}

// ---------------------------------------------------------------- 状态合并

func sameSet(a, b Set) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func consistent(t *APTA, blockOf map[string]int, syms []string) bool {
	for _, a := range syms {
		seen := map[int]int{}
		for s, b := range blockOf {
			nxt, ok := t.step(s, a)
			if !ok {
				continue
			}
			if prev, ok2 := seen[b]; ok2 && prev != blockOf[nxt] {
				return false
			}
			seen[b] = blockOf[nxt]
		}
	}
	return true
}

// MergeStates 贪心两两合并：label 不同绝不合块，其余只要不冲突就合。
func MergeStates(t *APTA, labels map[string]Set) (map[string]int, [][]string) {
	states := t.states()
	blockOf := map[string]int{}
	blocks := [][]string{}
	for _, s := range states {
		blockOf[s] = len(blocks)
		blocks = append(blocks, []string{s})
	}
	for changed := true; changed; {
		changed = false
		for i := 0; i < len(blocks) && !changed; i++ {
			for j := i + 1; j < len(blocks); j++ {
				if !sameSet(labels[blocks[i][0]], labels[blocks[j][0]]) {
					continue
				}
				trial := map[string]int{}
				for k, blk := range blocks {
					for _, s := range blk {
						if k == j {
							trial[s] = i
						} else {
							trial[s] = k
						}
					}
				}
				if !consistent(t, trial, t.types()) {
					continue
				}
				blocks[i] = append(blocks[i], blocks[j]...)
				blocks = append(blocks[:j], blocks[j+1:]...)
				blockOf = trial
				for k, blk := range blocks {
					for _, s := range blk {
						blockOf[s] = k
					}
				}
				changed = true
				break
			}
		}
	}
	return blockOf, blocks
}

// DFA 合并后的状态机；未定义转移落到 reject(-1)。
type DFA struct {
	Start int
	Trans map[[2]string]int
}

func buildDFA(t *APTA, blockOf map[string]int) *DFA {
	d := &DFA{Start: blockOf[""], Trans: map[[2]string]int{}}
	for s := range t.Children {
		for a, nxt := range t.Children[s] {
			d.Trans[[2]string{blockOf[s], a}] = blockOf[nxt]
		}
	}
	return d
}

func (d *DFA) accepts(seq []string) bool {
	q := d.Start
	for _, sym := range seq {
		n, ok := d.Trans[[2]string{q, sym}]
		if !ok {
			return false
		}
		q = n
	}
	return true
}

func main() {
	sessions := [][]string{
		{"login", "bot.dns", "bot.status", "mac.logout"},
		{"login", "mac.logout", "login", "bot.status", "bot.dns", "mac.logout"},
	}
	t := buildAPTA(sessions)
	types := t.types()
	prereq := InferPrerequisites(sessions, types)
	labels := LabelStates(t, prereq, EndTypes(sessions, types))
	for _, st := range t.states() {
		fmt.Printf("  label %-56s -> %v\n", st, labels[st])
	}
	blockOf, blocks := MergeStates(t, labels)
	dfa := buildDFA(t, blockOf)
	fmt.Println("merged blocks :", len(blocks), "+ reject")
	for _, s := range sessions {
		fmt.Println("  accept", s, "->", dfa.accepts(s))
	}
	fmt.Println("  reject [bot.dns] ->", dfa.accepts([]string{"bot.dns"}))

	a := Msg{"a", "in", newSet("GET"), newSet("recv"), newSet("read"), nil}
	b := Msg{"b", "out", newSet("PUT"), nil, nil, nil}
	fmt.Printf("distance(a,b) = %.6f\n", Distance(a, b))
}
