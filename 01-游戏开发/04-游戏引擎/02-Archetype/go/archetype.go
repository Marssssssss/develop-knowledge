// archetype.go — archetype（原型）存储的最小实现（与 python/archetype.py 同题）。
//
// Unity Entities 官方定义：archetype = 相同组件类型组合的唯一标识；chunk = 16KiB
// 均匀内存块，块内每组件一个数组 + 一个 entity id 数组，紧密排布；删除时把块内
// 最后一个实体搬来填空；块满则新建、块空则销毁。
package main

import (
	"fmt"
	"sort"
)

const chunkSize = 16 * 1024 // 16 KiB
const entityIDSize = 4      // demo 约定

var componentSize = map[string]int{"Position": 8, "Velocity": 8, "Health": 4}

// keyOf 把组件名集合规范成 archetype 的键（排序后拼接，与顺序无关）。
func keyOf(names []string) string {
	sorted := append([]string(nil), names...)
	sort.Strings(sorted)
	out := ""
	for i, n := range sorted {
		if i > 0 {
			out += "+"
		}
		out += n
	}
	return out
}

func memberList(key string) []string {
	if key == "" {
		return []string{}
	}
	out := []string{}
	cur := ""
	for i := 0; i < len(key); i++ {
		if key[i] == '+' {
			out = append(out, cur)
			cur = ""
			continue
		}
		cur += string(key[i])
	}
	return append(out, cur)
}

func entitySize(key string) int {
	total := entityIDSize
	for _, c := range memberList(key) {
		total += componentSize[c]
	}
	return total
}

func chunkCapacity(key string) int { return chunkSize / entitySize(key) }

type chunk struct {
	key      string
	capacity int
	arrays   map[string][]float64
	ids      []int
	count    int
}

func newChunk(key string) *chunk {
	cap := chunkCapacity(key)
	c := &chunk{key: key, capacity: cap, ids: make([]int, cap), count: 0}
	c.arrays = map[string][]float64{}
	for _, name := range memberList(key) {
		c.arrays[name] = make([]float64, cap)
	}
	return c
}

type archetype struct {
	key    string
	chunks []*chunk
}

func (a *archetype) freeChunk() *chunk {
	for _, c := range a.chunks {
		if c.count < c.capacity {
			return c
		}
	}
	c := newChunk(a.key)
	a.chunks = append(a.chunks, c)
	return c
}

type location struct {
	key   string
	chunk *chunk
	index int
}

type world struct {
	archetypes map[string]*archetype
	loc        map[int]*location
	comps      map[int]map[string]float64
	nextID     int
	copies     int
}

func newWorld() *world {
	return &world{
		archetypes: map[string]*archetype{},
		loc:        map[int]*location{},
		comps:      map[int]map[string]float64{},
	}
}

func (w *world) archetypeFor(key string) *archetype {
	a, ok := w.archetypes[key]
	if !ok {
		a = &archetype{key: key}
		w.archetypes[key] = a
	}
	return a
}

func (w *world) place(e int, a *archetype) {
	c := a.freeChunk()
	idx := c.count
	c.ids[idx] = e
	for name, arr := range c.arrays {
		arr[idx] = w.comps[e][name]
		w.copies++
	}
	c.count++
	w.loc[e] = &location{key: a.key, chunk: c, index: idx}
}

func (w *world) unplace(e int) {
	loc := w.loc[e]
	delete(w.loc, e)
	c := loc.chunk
	last := c.count - 1
	moved := c.ids[last]
	if moved != e { // swap-remove：块内最后一个实体填空
		c.ids[loc.index] = moved
		for name, arr := range c.arrays {
			arr[loc.index] = arr[last]
		}
		w.loc[moved] = &location{key: c.key, chunk: c, index: loc.index}
	}
	c.count--
}

func (w *world) create(comps map[string]float64) int {
	e := w.nextID
	w.nextID++
	names := []string{}
	for n := range comps {
		names = append(names, n)
	}
	w.comps[e] = comps
	a := w.archetypeFor(keyOf(names))
	w.place(e, a)
	return e
}

func (w *world) destroy(e int) {
	loc := w.loc[e]
	w.unplace(e)
	delete(w.comps, e)
	if loc.chunk.count == 0 {
		a := w.archetypes[loc.key]
		for i, c := range a.chunks {
			if c == loc.chunk {
				a.chunks = append(a.chunks[:i], a.chunks[i+1:]...)
				break
			}
		}
	}
}

// setComponent 覆盖组件值；name 为新组件时等价于 add 的结构变更。
func (w *world) setComponent(e int, name string, value float64) {
	w.comps[e][name] = value
	w.unplace(e)
	names := []string{}
	for n := range w.comps[e] {
		names = append(names, n)
	}
	w.place(e, w.archetypeFor(keyOf(names)))
}

func (w *world) removeComponent(e int, name string) {
	delete(w.comps[e], name)
	w.unplace(e)
	names := []string{}
	for n := range w.comps[e] {
		names = append(names, n)
	}
	w.place(e, w.archetypeFor(keyOf(names)))
}

func (w *world) query(want ...string) []int {
	out := []int{}
	for key, a := range w.archetypes {
		if !superset(key, want) {
			continue
		}
		for _, c := range a.chunks {
			out = append(out, c.ids[:c.count]...)
		}
	}
	return out
}

func superset(key string, want []string) bool {
	have := map[string]bool{}
	for _, n := range memberList(key) {
		have[n] = true
	}
	for _, n := range want {
		if !have[n] {
			return false
		}
	}
	return true
}

func main() {
	capPV := chunkCapacity(keyOf([]string{"Position", "Velocity"}))
	fmt.Printf("chunk 容量(Position+Velocity) = %d (16KiB / %d B)\n", capPV,
		entitySize(keyOf([]string{"Position", "Velocity"})))

	w := newWorld()
	ids := []int{}
	for i := 0; i < capPV+1; i++ {
		ids = append(ids, w.create(map[string]float64{"Position": float64(i), "Velocity": 1}))
	}
	a := w.archetypes[keyOf([]string{"Position", "Velocity"})]
	fmt.Printf("chunk 数 = %d, 首块占用 = %d/%d\n", len(a.chunks), a.chunks[0].count, a.chunks[0].capacity)

	filler := a.chunks[0].ids[a.chunks[0].count-1]
	w.destroy(a.chunks[0].ids[0])
	fmt.Printf("swap-remove 后 ids[0] = %d (filler=%d), count=%d, loc[ filler ].index=%d\n",
		a.chunks[0].ids[0], filler, a.chunks[0].count, w.loc[filler].index)

	w2 := newWorld()
	e := w2.create(map[string]float64{"Position": 0, "Velocity": 2})
	before := w2.copies
	w2.setComponent(e, "Health", 10)
	fmt.Printf("加组件搬移 %d 个组件值, 新 archetype = %s\n", w2.copies-before, w2.loc[e].key)
	before = w2.copies
	w2.removeComponent(e, "Health")
	fmt.Printf("删组件搬移 %d 个组件值, 回到 archetype = %s\n", w2.copies-before, w2.loc[e].key)

	w3 := newWorld()
	x := w3.create(map[string]float64{"Position": 1})
	y := w3.create(map[string]float64{"Position": 2, "Velocity": 2})
	w3.create(map[string]float64{"Health": 3})
	got := w3.query("Position")
	fmt.Printf("query(Position) = %v (期望含 %d,%d)\n", got, x, y)
	fmt.Printf("query(Position,Velocity) = %v\n", w3.query("Position", "Velocity"))
}
