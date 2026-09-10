// ecs_demo.go — 最小 sparse set ECS（Entity Component System）演示。
//
// 存储：每类组件一个 sparse set
//
//	sparse : entity id -> dense 下标（-1 表示无该组件）
//	dense  : dense 下标 -> entity id
//	comps  : dense 下标 -> 组件数据（与 dense 一一对应）
//
// 演示：8 实体（偶数号 P+V，奇数号仅 P）-> 3 帧 movement ->
//	销毁实体 2（swap-remove）-> 打印内部布局
package main

import "fmt"

const (
	numEntities = 8
	frames      = 3
	dt          = 0.016 // 帧间隔（秒），教学用固定值
)

// Entity 只是一个整数 ID（生产实现会打包 generation 代数）。
type Entity int

// Component：纯数据。
type Position struct{ X, Y float64 }
type Velocity struct{ X, Y float64 }

// SparseSet 单类组件的 sparse set 存储（EnTT 路线），Go 泛型实现。
type SparseSet[C any] struct {
	sparse []int      // entity -> dense 下标，-1 = 无
	dense  []Entity   // dense 下标 -> entity
	comps  []C        // dense 下标 -> 组件
}

func newSparseSet[C any](maxEntities int) *SparseSet[C] {
	s := &SparseSet[C]{sparse: make([]int, maxEntities)}
	for i := range s.sparse {
		s.sparse[i] = -1
	}
	return s
}

func (s *SparseSet[C]) Len() int     { return len(s.dense) }
func (s *SparseSet[C]) has(e Entity) bool {
	return e >= 0 && int(e) < len(s.sparse) && s.sparse[e] >= 0
}

func (s *SparseSet[C]) add(e Entity, comp C) {
	if s.has(e) { // 已存在则覆盖
		s.comps[s.sparse[e]] = comp
		return
	}
	s.sparse[e] = len(s.dense)
	s.dense = append(s.dense, e)
	s.comps = append(s.comps, comp)
}

// remove O(1) swap-remove：尾元素填补空洞并回写其 sparse 索引。
func (s *SparseSet[C]) remove(e Entity) {
	idx := s.sparse[e]
	last := len(s.dense) - 1
	moved := s.dense[last]
	s.dense[idx] = moved
	s.comps[idx] = s.comps[last]
	s.dense = s.dense[:last]
	s.comps = s.comps[:last]
	s.sparse[e] = -1
	if idx != last {
		s.sparse[moved] = idx
	}
}

func (s *SparseSet[C]) get(e Entity) *C { return &s.comps[s.sparse[e]] }

// World 实体分配 + 两组件集合（教学版，无 generation）。
type World struct {
	next       Entity
	alive      []bool
	positions  *SparseSet[Position]
	velocities *SparseSet[Velocity]
}

func newWorld(maxEntities int) *World {
	return &World{
		alive:      make([]bool, maxEntities),
		positions:  newSparseSet[Position](maxEntities),
		velocities: newSparseSet[Velocity](maxEntities),
	}
}

func (w *World) create() Entity {
	e := w.next
	w.next++
	w.alive[e] = true
	return e
}

func (w *World) destroy(e Entity) {
	if w.positions.has(e) {
		w.positions.remove(e)
	}
	if w.velocities.has(e) {
		w.velocities.remove(e)
	}
	w.alive[e] = false
}

// movementSystem 查询 P+V 组合：遍历较短的集合做成员测试。
func movementSystem(w *World) {
	p, v := w.positions, w.velocities
	base := v // 取最小集合
	if p.Len() < v.Len() {
		base = p
	}
	for _, e := range append([]Entity(nil), base.dense...) { // 快照防增删干扰
		if !p.has(e) || !v.has(e) {
			continue
		}
		pos, vel := p.get(e), v.get(e)
		pos.X += vel.X * dt
		pos.Y += vel.Y * dt
	}
}

func printLayout(name string, s *SparseSet[Position]) {
	fmt.Printf("  %s: dense=%v sparse=%v\n", name, s.dense, s.sparse[:numEntities])
}

func printPositions(w *World, tag string) {
	fmt.Println(tag)
	for e := Entity(0); e < numEntities; e++ {
		switch {
		case !w.alive[e]:
			fmt.Printf("  e%d: <destroyed>\n", e)
		case !w.positions.has(e):
			fmt.Printf("  e%d: (no position)\n", e)
		default:
			p := w.positions.get(e)
			mark := "  [P]"
			if w.velocities.has(e) {
				mark = "  [P+V]"
			}
			fmt.Printf("  e%d: pos=(%.2f, %.2f)%s\n", e, p.X, p.Y, mark)
		}
	}
}

func main() {
	w := newWorld(numEntities * 2) // 余量给演示

	// 偶数号实体挂 P+V，奇数号只挂 P
	for e := Entity(0); e < numEntities; e++ {
		w.create()
		w.positions.add(e, Position{X: float64(e)})
		if e%2 == 0 {
			w.velocities.add(e, Velocity{X: 1, Y: 2})
		}
	}

	printPositions(w, "--- frame 0 (initial) ---")
	for f := 1; f <= frames; f++ {
		movementSystem(w)
		fmt.Printf("--- after frame %d ---\n", f)
		for e := Entity(0); e < numEntities; e += 2 { // 只看带速度的
			p := w.positions.get(e)
			fmt.Printf("  e%d: pos=(%.2f, %.2f)\n", e, p.X, p.Y)
		}
	}

	// 销毁实体 2：触发 swap-remove，观察 dense 紧凑性
	fmt.Println("--- destroy e2 (swap-remove) ---")
	w.destroy(2)
	printPositions(w, "positions after destroy:")
	fmt.Println("sparse set internals:")
	printLayout("Position ", w.positions.dense, w.positions.sparse)
	printLayout("Velocity", w.velocities.dense, w.velocities.sparse)
}
