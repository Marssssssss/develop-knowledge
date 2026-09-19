// Svelte 5 runes 运行时的 Go 复刻（与 svelte_runes.py 同题，用于跨语言对照）。
// 依据同 Python 版：sveltejs/svelte documentation/docs/02-runes/*.md
package main

import (
	"fmt"
	"reflect"
)

type collector interface {
	record(s *Source, version int)
	stale() bool
	notify()
}

var cur []collector
var queue []*Effect

// depSet 用「指针」做键：用 version 当键会在值变化时把旧依赖永久留在表里。
type depSet struct {
	src map[*Source]int
	der map[*Derived]int
}

func newDepSet() depSet {
	return depSet{src: map[*Source]int{}, der: map[*Derived]int{}}
}

// ---------- Source：$state 的原子槽位 ----------

type Source struct {
	value   interface{}
	version int
	subs    []collector
}

func NewSource(v interface{}) *Source { return &Source{value: v} }

func (s *Source) sub(c collector) {
	for _, x := range s.subs {
		if x == c {
			return
		}
	}
	s.subs = append(s.subs, c)
}

func (s *Source) set(v interface{}) bool {
	if reflect.DeepEqual(s.value, v) {
		return false
	}
	s.value = v
	s.version++
	for _, c := range s.subs {
		c.notify()
	}
	return true
}

func read(s *Source) interface{} {
	if len(cur) > 0 {
		c := cur[len(cur)-1]
		c.record(s, s.version)
		s.sub(c)
	}
	return s.value
}

// ---------- Derived：$derived，pull 语义 ----------

type Derived struct {
	fn         func() interface{}
	value      interface{}
	version    int
	dirty      bool
	deps       depSet
	subscribed []*Source
	downstream []*Effect
	Recomputes int
}

func NewDerived(fn func() interface{}) *Derived {
	return &Derived{fn: fn, dirty: true, deps: newDepSet()}
}

func (d *Derived) record(s *Source, version int) {
	d.deps.src[s] = version
	d.subscribed = append(d.subscribed, s)
}

func (d *Derived) unsubscribe() {
	for _, s := range d.subscribed {
		for i, c := range s.subs {
			if c == collector(d) {
				s.subs = append(s.subs[:i], s.subs[i+1:]...)
				break
			}
		}
	}
	d.subscribed = nil
}

func staleOf(ds depSet) bool {
	for s, v := range ds.src {
		if s.version != v {
			return true
		}
	}
	for x, v := range ds.der {
		if x.version != v {
			return true
		}
	}
	return false
}

func (d *Derived) stale() bool { return staleOf(d.deps) }

func (d *Derived) notify() {
	d.dirty = true
	for _, e := range d.downstream {
		schedule(e)
	}
}

func (d *Derived) Get() interface{} {
	if d.dirty || d.stale() {
		d.recompute()
	}
	if len(cur) > 0 {
		if e, ok := cur[len(cur)-1].(*Effect); ok {
			e.deps.der[d] = d.version
			d.downstream = append(d.downstream, e)
		}
	}
	return d.value
}

func (d *Derived) recompute() {
	d.unsubscribe()
	d.deps = newDepSet()
	cur = append(cur, d)
	newVal := d.fn()
	cur = cur[:len(cur)-1]
	d.dirty = false
	d.Recomputes++
	if !reflect.DeepEqual(newVal, d.value) {
		d.value = newVal
		d.version++
	}
}

// ---------- Effect：$effect ----------

type Effect struct {
	fn         func() func()
	prio       int
	teardown   func()
	deps       depSet
	subscribed []*Source
	Runs       int
	TdRuns     int
}

func NewEffect(fn func() func(), prio int) *Effect {
	return &Effect{fn: fn, prio: prio, deps: newDepSet()}
}

func (e *Effect) record(s *Source, version int) {
	e.deps.src[s] = version
	e.subscribed = append(e.subscribed, s)
}

func (e *Effect) unsubscribe() {
	for _, s := range e.subscribed {
		for i, c := range s.subs {
			if c == collector(e) {
				s.subs = append(s.subs[:i], s.subs[i+1:]...)
				break
			}
		}
	}
	e.subscribed = nil
}

func (e *Effect) stale() bool { return staleOf(e.deps) }

func (e *Effect) notify() { schedule(e) }

func (e *Effect) Run() {
	e.unsubscribe()
	if e.teardown != nil {
		e.teardown()
		e.teardown = nil
		e.TdRuns++
	}
	e.deps = newDepSet()
	cur = append(cur, e)
	r := e.fn()
	cur = cur[:len(cur)-1]
	e.Runs++
	if r != nil {
		e.teardown = r
	}
}

func schedule(e *Effect) {
	for _, x := range queue {
		if x == e {
			return
		}
	}
	queue = append(queue, e)
}

// Flush 对应 microtask 边界：模板 effect 先跑，且 derived 先求值再判断是否真过期。
func Flush() {
	for round := 0; round < 16; round++ {
		if len(queue) == 0 {
			return
		}
		pending := queue
		queue = nil
		for i := 0; i < len(pending); i++ {
			for j := i + 1; j < len(pending); j++ {
				if pending[j].prio < pending[i].prio {
					pending[i], pending[j] = pending[j], pending[i]
				}
			}
		}
		for _, e := range pending {
			for x := range e.deps.der {
				x.Get()
			}
			if e.stale() {
				e.Run()
			}
		}
	}
}

func main() {
	n := NewSource(1)
	d := NewDerived(func() interface{} { return read(n).(int) * 2 })
	fmt.Println("recomputes before first read:", d.Recomputes)
	fmt.Println("get:", d.Get(), "recomputes:", d.Recomputes)
	n.set(2)
	fmt.Println("after set, recomputes stays (pull):", d.Recomputes)
	fmt.Println("get:", d.Get(), "recomputes:", d.Recomputes)

	// 批处理：同一次 tick 内改两次只重跑一次
	log := []int{}
	c1 := NewSource(1)
	e := NewEffect(func() func() {
		log = append(log, read(c1).(int))
		return nil
	}, 1)
	e.Run()
	c1.set(2)
	c1.set(3)
	Flush()
	fmt.Println("effect runs (batched):", e.Runs, "log:", log)
}
