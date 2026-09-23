package main

import "fmt"

func main() {
	fmt.Println("== 1. claripy 位序：a[31] 是最左位 ==")
	a := BVV(0x7FFFFFFF, 32)
	fmt.Printf("  0x7fffffff[31]=%d  [0]=%d\n", Eval(Index(a, 31, 31), nil), Eval(Index(a, 0, 0), nil))
	w := BVV(0x01020304, 32)
	parts := []int{}
	for _, p := range Chop(w, 8) {
		parts = append(parts, Eval(p, nil))
	}
	fmt.Printf("  chop(8) = %#v\n", parts)
	bs := []int{}
	for i := 0; i < 4; i++ {
		bs = append(bs, Eval(GetByte(w, i), nil))
	}
	fmt.Printf("  get_byte = %#v\n", bs)

	fmt.Println("== 2. 整数被强制成左操作数的位宽 ==")
	fmt.Printf("  BVV(1,8)+300 -> %d（位宽 %d）\n", Eval(Add(BVV(1, 8), 300), nil), Add(BVV(1, 8), 300).Size)

	fmt.Println("== 3. 约束求解 ==")
	sv := NewSimSolver()
	x := sv.BVS("x", 8)
	sv.Add(Gt(x, 3), Lt(x, 10))
	fmt.Printf("  x>3, x<10 -> %v\n", sv.EvalUpTo(x, 20))

	fmt.Println("== 4. 路径爆炸 ==")
	m := NewSimulationManager([]*SimState{NewSimState(0)}, func(s *SimState) []*SimState {
		out := []*SimState{}
		for i := 0; i < 2; i++ {
			ns := s.Copy()
			ns.Addr = s.Addr + i + 1
			out = append(out, ns)
		}
		return out
	})
	for i := 0; i < 5; i++ {
		m.Step()
		fmt.Printf("  第 %d 步: active=%d\n", i+1, len(m.Stashes["active"]))
	}

	fmt.Println("== 5. explore 的 num_find 累加 ==")
	m2 := NewSimulationManager([]*SimState{NewSimState(0)}, func(s *SimState) []*SimState {
		if s.Addr >= 100 {
			return nil
		}
		ns := s.Copy()
		ns.Addr = s.Addr + 1
		return []*SimState{ns}
	})
	m2.Explore(func(s *SimState) bool { return s.Addr == 4 }, 1, "found")
	fmt.Printf("  find=4 -> found=%d 个\n", len(m.Stashes["found"]))
	fmt.Printf("  found 数=%d\n", len(m2.Stashes["found"]))
	m2.Stashes["found"] = append(m2.Stashes["found"], NewSimState(99))
	m2.Explore(func(s *SimState) bool { return s.Addr == 5 }, 1, "found")
	fmt.Printf("  已有 1 个后再 explore -> found=%d 个\n", len(m2.Stashes["found"]))

	fmt.Println("== 6. stash 搬移 ==")
	m3 := NewSimulationManager([]*SimState{}, nil)
	for i := 0; i < 6; i++ {
		m3.Stashes["active"] = append(m3.Stashes["active"], NewSimState(i))
	}
	m3.Move("active", "stashed", func(s *SimState) bool { return s.Addr < 3 })
	m3.Move("active", Drop, func(s *SimState) bool { return s.Addr == 4 })
	fmt.Printf("  active=%d stashed=%d\n", len(m3.Stashes["active"]), len(m3.Stashes["stashed"]))
}
