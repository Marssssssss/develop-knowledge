package main

import (
	"sort"
	"fmt"
	"strings"
)

// ---------------------------------------------------------------- 状态

type state struct {
	values map[string]any
}

func newState(v map[string]any) *state {
	cp := map[string]any{}
	for k, val := range v {
		cp[k] = val
	}
	return &state{values: cp}
}

func (s *state) clone() *state          { return newState(s.values) }
func (s *state) count() int             { return len(s.values) }
func (s *state) addFrom(o *state)       { for k, v := range o.values { s.values[k] = v } }
func (s *state) get(k string) any       { return s.values[k] }

// key 把状态序列化成稳定字符串：Go 的 map 遍历无序，不能直接拿 map 当键
func (s *state) key() string {
	keys := make([]string, 0, len(s.values))
	for k := range s.values {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	var b strings.Builder
	for _, k := range keys {
		fmt.Fprintf(&b, "%s=%v;", k, s.values[k])
	}
	return b.String()
}

func (s *state) hasAny(other *state) bool {
	for k, v := range other.values {
		if isMatch(v, s.values[k]) {
			return true
		}
	}
	return false
}

func (s *state) hasAnyConflict(other *state) bool {
	for k, ov := range other.values {
		if _, ok := s.values[k]; !ok {
			continue
		}
		if !areCompatible(ov, s.values[k]) {
			return true
		}
	}
	return false
}

// hasAnyConflictRelaxed：被 changes 修掉的冲突不算冲突
func (s *state) hasAnyConflictRelaxed(changes, other *state) bool {
	for k, ov := range other.values {
		if _, ok := s.values[k]; !ok {
			continue
		}
		if !areCompatible(ov, s.values[k]) && !areCompatible(changes.values[k], s.values[k]) {
			return true
		}
	}
	return false
}

func (s *state) missingDifference(other *state, into *state) int {
	count := 0
	for k, v := range s.values {
		if !isMatch(v, other.values[k]) {
			count++
			if into != nil {
				into.values[k] = v
			}
		}
	}
	return count
}

func (s *state) replaceWithMissingDifference(other *state) {
	kept := map[string]any{}
	for k, v := range s.values {
		if !isMatch(v, other.values[k]) {
			kept[k] = v
		}
	}
	s.values = kept
}
