package main

import (
	"sort"
	"strings"
)

// ---------------------------------------------------------------- APTA

// APTA 前缀树；状态用 "a|b|c" 这样的路径串表示，root 是空串。
type APTA struct {
	Children map[string]map[string]string
	Sessions [][]string
}

func buildAPTA(sessions [][]string) *APTA {
	t := &APTA{Children: map[string]map[string]string{"": {}}, Sessions: sessions}
	for _, s := range sessions {
		node := ""
		for _, sym := range s {
			nxt := node + "|" + sym
			if _, ok := t.Children[nxt]; !ok {
				t.Children[nxt] = map[string]string{}
			}
			t.Children[node][sym] = nxt
			node = nxt
		}
	}
	return t
}

func (t *APTA) step(state, sym string) (string, bool) {
	n, ok := t.Children[state][sym]
	return n, ok
}

func (t *APTA) states() []string {
	out := make([]string, 0, len(t.Children))
	for k := range t.Children {
		out = append(out, k)
	}
	sort.Slice(out, func(i, j int) bool {
		if len(out[i]) != len(out[j]) {
			return len(out[i]) < len(out[j])
		}
		return out[i] < out[j]
	})
	return out
}

func (t *APTA) types() []string {
	m := map[string]bool{}
	for _, s := range t.Sessions {
		for _, x := range s {
			m[x] = true
		}
	}
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func pathOf(state string) []string {
	if state == "" {
		return nil
	}
	return strings.Split(state, "|")
}
