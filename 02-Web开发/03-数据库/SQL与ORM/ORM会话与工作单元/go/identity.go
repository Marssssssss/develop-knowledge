package main

import (
	"fmt"
	"sort"
)


func newObj(kind string, id int, fields map[string]string) *Obj {
	return &Obj{Kind: kind, ID: id, Fields: fields, Alive: true}
}

func (o *Obj) identityKey() Key {
	return Key(fmt.Sprintf("%s|%d", o.Kind, o.ID))
}

// State 对应 InstanceState：保存已提交快照用于脏检查。
type State struct {
	Obj         *Obj
	Key         Key
	Committed   map[string]string
	Deleted     bool
	HasIdentity bool
	Session     *Session
}

func newState(o *Obj) *State {
	return &State{Obj: o, Committed: map[string]string{}}
}

func (s *State) attrs() map[string]string { return s.Obj.Fields }

// dirtyAttrs 与官方 attributes.get_history 同口径：只比已提交快照。
func (s *State) dirtyAttrs() []string {
	var out []string
	for k, v := range s.attrs() {
		if old, ok := s.Committed[k]; !ok || old != v {
			out = append(out, k)
		}
	}
	sort.Strings(out)
	return out
}

func (s *State) isDirty() bool { return len(s.dirtyAttrs()) > 0 }

// IdentityMap 是身份映射。
type IdentityMap struct{ dict map[Key]*State }

func newIdentityMap() *IdentityMap { return &IdentityMap{dict: map[Key]*State{}} }

// Add 对应官方 _WeakInstanceDict.add：另一个**活着**的同键对象存在时报错，
// 同一个 state 重复登记返回 false。
func (m *IdentityMap) Add(st *State) (bool, error) {
	if ex, ok := m.dict[st.Key]; ok {
		if ex != st {
			if ex.Obj.Alive {
				return false, fmt.Errorf(
					"Can't attach instance %s; another instance with key %s is already present",
					st.Obj.Kind, st.Key)
			}
		} else {
			return false, nil
		}
	}
	m.dict[st.Key] = st
	return true, nil
}

// Replace 对应官方 replace：不报错，直接换掉。
func (m *IdentityMap) Replace(st *State) *State {
	ex := m.dict[st.Key]
	m.dict[st.Key] = st
	return ex
}

func (m *IdentityMap) Get(k Key) *State {
	st, ok := m.dict[k]
	if !ok || !st.Obj.Alive {
		return nil
	}
	return st
}

func (m *IdentityMap) Len() int {
	n := 0
	for _, st := range m.dict {
		if st.Obj.Alive {
			n++
		}
	}
	return n
}

func (m *IdentityMap) liveStates() []*State {
	var out []*State
	for _, st := range m.dict {
		if st.Obj.Alive {
			out = append(out, st)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Key < out[j].Key })
	return out
}
