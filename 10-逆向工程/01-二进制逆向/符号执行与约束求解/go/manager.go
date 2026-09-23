package main

// IntegralStashes 是 angr 的 _integral_stashes，顺序即源码顺序。
var IntegralStashes = []string{
	"active", "stashed", "pruned", "unsat", "errored", "deadended", "unconstrained",
}

// All / Drop 是 SimulationManager 的两个哨兵。
const (
	All  = "_ALL"
	Drop = "_DROP"
)

// SimSolver 是简化版约束求解器。
type SimSolver struct {
	Constraints []*BV
	Sizes       map[string]int
}

// NewSimSolver 建一个空求解器。
func NewSimSolver() *SimSolver {
	return &SimSolver{Sizes: map[string]int{}}
}

// BVS 建符号并登记位宽。
func (s *SimSolver) BVS(name string, size int) *BV {
	s.Sizes[name] = size
	return BVS(name, size)
}

// Add 追加约束。
func (s *SimSolver) Add(cs ...*BV) {
	s.Constraints = append(s.Constraints, cs...)
}

// Satisfiable 判可解。
func (s *SimSolver) Satisfiable() bool {
	return len(Solve(s.Constraints, s.symbols(), 1)) > 0
}

func (s *SimSolver) symbols() []string {
	out := []string{}
	for _, c := range s.Constraints {
		out = append(out, Symbols(c)...)
	}
	seen := map[string]bool{}
	res := []string{}
	for _, x := range out {
		if !seen[x] {
			seen[x] = true
			res = append(res, x)
		}
	}
	return res
}

// EvalUpTo 求至多 n 个解下表达式的值。
func (s *SimSolver) EvalUpTo(expr *BV, n int) []int {
	syms := append(Symbols(expr), s.symbols())
	syms = dedup(syms)
	out := []int{}
	for _, env := range Solve(s.Constraints, syms, n) {
		out = append(out, Eval(expr, env))
	}
	return out
}

func dedup(xs []string) []string {
	seen := map[string]bool{}
	res := []string{}
	for _, x := range xs {
		if !seen[x] {
			seen[x] = true
			res = append(res, x)
		}
	}
	return res
}

// SimState 是插件化状态。
type SimState struct {
	Addr   int
	Solver *SimSolver
	Regs   map[string]int
	Memory map[int]int
	Plugin map[string]bool
}

// NewSimState 建状态并登记默认插件。
func NewSimState(addr int) *SimState {
	return &SimState{
		Addr:   addr,
		Solver: NewSimSolver(),
		Regs:   map[string]int{},
		Memory: map[int]int{},
		Plugin: map[string]bool{
			"regs": true, "registers": true, "mem": true, "memory": true,
			"solver": true, "inspect": true, "history": true, "scratch": true,
			"posix": true, "fs": true, "libc": true, "heap": true, "callstack": true,
		},
	}
}

// Copy 拷贝一份互不影响的状态。
func (s *SimState) Copy() *SimState {
	ns := NewSimState(s.Addr)
	for k, v := range s.Regs {
		ns.Regs[k] = v
	}
	ns.Solver = s.Solver
	return ns
}

// SimulationManager 是 stash 状态机。
type SimulationManager struct {
	Stashes   map[string][]*SimState
	SaveUnsat bool
	StepFunc  func(*SimState) []*SimState
	Steps     int
}

// NewSimulationManager 建管理器，预置 integral stashes。
func NewSimulationManager(active []*SimState, step func(*SimState) []*SimState) *SimulationManager {
	m := &SimulationManager{Stashes: map[string][]*SimState{}, StepFunc: step}
	for _, k := range IntegralStashes {
		m.Stashes[k] = []*SimState{}
	}
	m.Stashes["active"] = append(m.Stashes["active"], active...)
	return m
}

// Move 按 filter 在 stash 之间搬移。
func (m *SimulationManager) Move(from, to string, filter func(*SimState) bool) []*SimState {
	moved, kept := []*SimState{}, []*SimState{}
	for _, s := range m.Stashes[from] {
		if filter == nil || filter(s) {
			moved = append(moved, s)
		} else {
			kept = append(kept, s)
		}
	}
	m.Stashes[from] = kept
	if to != Drop {
		m.Stashes[to] = append(m.Stashes[to], moved...)
	}
	return moved
}

// Step 推进一轮：无后继进 deadended，不可解按 SaveUnsat 处理。
func (m *SimulationManager) Step() {
	succ, dead := []*SimState{}, []*SimState{}
	for _, s := range m.Stashes["active"] {
		out := []*SimState{}
		if m.StepFunc != nil {
			out = m.StepFunc(s)
		}
		if len(out) == 0 {
			dead = append(dead, s)
			continue
		}
		succ = append(succ, out...)
	}
	m.Stashes["active"] = succ
	m.Stashes["deadended"] = append(m.Stashes["deadended"], dead...)
	keep := []*SimState{}
	for _, s := range m.Stashes["active"] {
		if s.Solver.Satisfiable() {
			keep = append(keep, s)
		} else if m.SaveUnsat {
			m.Stashes["unsat"] = append(m.Stashes["unsat"], s)
		}
	}
	m.Stashes["active"] = keep
	m.Steps++
}

// Explore 复刻 explore：num_find 先把已有 found 数量加上。
func (m *SimulationManager) Explore(find func(*SimState) bool, numFind int, findStash string) {
	numFind += len(m.Stashes[findStash])
	if _, ok := m.Stashes[findStash]; !ok {
		m.Stashes[findStash] = []*SimState{}
	}
	for {
		if len(m.Stashes[findStash]) >= numFind {
			break
		}
		if len(m.Stashes["active"]) == 0 {
			break
		}
		m.Step()
		m.Move("active", findStash, find)
	}
}
