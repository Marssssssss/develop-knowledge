// nodelifecycle.go — Godot 节点生命周期与帧处理顺序的最小模型（与 python/nodelifecycle.py 同题）。
//
// 事实来源：Godot 官方 class_node / nodes_and_scene_instances / idle_and_physics_processing
// 三篇文档。要点：_enter_tree 父先于子；_ready 子先于父（逆序，且 @onready 在其之前）；
// queue_free 延迟到当前帧处理结束后并连带释放子节点；_process 随帧率而
// _physics_process 固定 60 次/秒，两者不同步。
package main

import (
	"fmt"
	"strings"
)

const (
	physicsFPS = 60
	stepUS     = 1000000 / physicsFPS // 16.666 ms
)

type node struct {
	name     string
	world    *world
	parent   *node
	children []*node
	inTree   bool
	ready    bool
	alive    bool
	process  bool
	physics  bool
	pCalls   int
	yCalls   int
	deltas   []float64
}

func newNode(name string, w *world, physics bool) *node {
	return &node{name: name, world: w, alive: true, process: true, physics: physics}
}

type world struct {
	root         *node
	log          []string
	pendingFree  []*node
	physicsAcc   int
}

func newWorld() *world {
	w := &world{}
	w.root = newNode("root", w, false)
	w.root.inTree = true
	w.root.ready = true
	return w
}

func (w *world) enterTree(n *node) { // 自顶向下：父先于子
	n.inTree = true
	w.log = append(w.log, n.name+":enter_tree")
	for _, c := range append([]*node{}, n.children...) {
		w.enterTree(c)
	}
}

func (w *world) markReady(n *node) { // 自底向上：子先于父
	for _, c := range append([]*node{}, n.children...) {
		w.markReady(c)
	}
	w.log = append(w.log, n.name+":onready") // @onready 在 _ready 之前
	n.ready = true
	w.log = append(w.log, n.name+":ready")
}

func (w *world) exitTree(n *node) {
	n.inTree = false
	w.log = append(w.log, n.name+":exit_tree")
	for _, c := range append([]*node{}, n.children...) {
		w.exitTree(c)
	}
}

func (n *node) addChild(c *node) {
	if c.parent != nil {
		c.parent.removeChild(c)
	}
	c.parent = n
	n.children = append(n.children, c)
	if n.inTree {
		n.world.enterTree(c)
		n.world.markReady(c)
	}
}

func (n *node) removeChild(c *node) {
	for i, x := range n.children {
		if x == c {
			n.children = append(n.children[:i], n.children[i+1:]...)
			break
		}
	}
	c.parent = nil
	if c.inTree {
		n.world.exitTree(c)
	}
}

func (n *node) queueFree() { n.world.pendingFree = append(n.world.pendingFree, n) }

func (w *world) freeNode(n *node) {
	if !n.alive {
		return
	}
	if n.parent != nil {
		n.parent.removeChild(n)
	}
	if n.inTree {
		w.exitTree(n)
	}
	for _, c := range append([]*node{}, n.children...) { // 连带释放所有子节点
		w.freeNode(c)
	}
	n.children = nil
	n.alive = false
	w.log = append(w.log, n.name+":free")
}

func (w *world) all(n *node) []*node {
	out := []*node{n}
	for _, c := range n.children {
		out = append(out, w.all(c)...)
	}
	return out
}

// frame 推进一帧：先按固定步长跑物理，再跑一次 idle，最后处理 queue_free。
func (w *world) frame(dtUS int) int {
	w.physicsAcc += dtUS
	steps := 0
	for w.physicsAcc >= stepUS {
		w.physicsAcc -= stepUS
		steps++
		for _, n := range w.all(w.root) {
			if n.alive && n.inTree && n.physics {
				n.yCalls++
			}
		}
	}
	for _, n := range w.all(w.root) {
		if n.alive && n.inTree && n.process {
			n.pCalls++
			n.deltas = append(n.deltas, float64(dtUS)/1e6)
		}
	}
	for _, n := range append([]*node{}, w.pendingFree...) {
		w.freeNode(n)
	}
	w.pendingFree = nil
	return steps
}

func pacing(totalUS, frameUS int) []int {
	out := []int{}
	for left := totalUS; left > 0; {
		s := frameUS
		if s > left {
			s = left
		}
		out = append(out, s)
		left -= s
	}
	return out
}

func main() {
	w := newWorld()
	parent := newNode("parent", w, false)
	child := newNode("child", w, false)
	grand := newNode("grand", w, false)
	child.addChild(grand)
	parent.addChild(child)
	w.root.addChild(parent)
	fmt.Printf("回调顺序 = %s\n", strings.Join(w.log, " → "))

	w2 := newWorld()
	detached := newNode("detached", w2, false)
	kid := newNode("kid", w2, false)
	detached.addChild(kid)
	fmt.Printf("挂到游离父节点时回调数 = %d（应为 0）\n", len(w2.log))
	w2.root.addChild(detached)
	fmt.Printf("补挂后 = %s\n", strings.Join(w2.log, " → "))

	w3 := newWorld()
	a := newNode("a", w3, true)
	ac := newNode("a_child", w3, false)
	a.addChild(ac)
	w3.root.addChild(a)
	a.queueFree()
	w3.frame(stepUS)
	fmt.Printf("queue_free：本帧 _process=%d，帧末 alive=%v，子节点 alive=%v\n",
		a.pCalls, a.alive, ac.alive)

	w4 := newWorld()
	d := newNode("d", w4, true)
	w4.root.addChild(d)
	for _, dt := range pacing(1000000, 16666) {
		w4.frame(dt)
	}
	w5 := newWorld()
	e := newNode("e", w5, true)
	w5.root.addChild(e)
	for _, dt := range pacing(1000000, 33333) {
		w5.frame(dt)
	}
	fmt.Printf("1 秒：60fps → process=%d physics=%d；30fps → process=%d physics=%d\n",
		d.pCalls, d.yCalls, e.pCalls, e.yCalls)

	w6 := newWorld()
	g := newNode("g", w6, true)
	w6.root.addChild(g)
	s1 := w6.frame(1000) // 1ms：不足一个物理步
	s2 := w6.frame(33333)
	fmt.Printf("短帧物理步=%d（process 仍为 1），长帧物理步=%d\n", s1, s2)
}
