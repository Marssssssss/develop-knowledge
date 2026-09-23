package main

import "fmt"

func main() {
	key := make([]byte, 16)
	for i := range key {
		key[i] = byte(i)
	}

	fmt.Println("== 1. case 值从哪来 ==")
	for i := 0; i < 5; i++ {
		fmt.Printf("  scramble32(%d, key) = 0x%08x\n", i, Scramble32(i, key))
	}
	fmt.Printf("  全零 key: scramble32(0) = 0x%08x\n", Scramble32(0, make([]byte, 16)))

	fmt.Println("== 2. 平坦化一个菱形 CFG ==")
	cfg := &CFG{Entry: "entry", Order: []string{"entry", "B1", "B2", "B3"},
		Blocks: map[string]*Block{
			"entry": {Name: "entry", Term: Br, Succ: []string{"B1", "B2"}, Cond: "c0"},
			"B1":    {Name: "B1", Term: Jmp, Succ: []string{"B3"}},
			"B2":    {Name: "B2", Term: Jmp, Succ: []string{"B3"}},
			"B3":    {Name: "B3", Term: Ret},
		}}
	f := Flatten(cfg, key)
	if !f.OK {
		panic("flatten failed: " + f.Reason)
	}
	for _, n := range f.Order {
		fmt.Printf("    case 0x%08x -> %-12s %+v\n", f.CaseOf[n], n, f.Trans[n])
	}
	fmt.Printf("  switchVar 初值 = 0x%08x\n", f.Initial)

	fmt.Println("== 3. 状态机执行 ==")
	for _, c0 := range []bool{true, false} {
		seen, how := RunFlattened(cfg, f, map[string]bool{"c0": c0}, 100)
		fmt.Printf("  c0=%-5v -> %v (%s)\n", c0, seen, how)
	}

	fmt.Println("== 4. 回边兜底 ==")
	bcfg := &CFG{Entry: "e", Order: []string{"e", "A", "B"},
		Blocks: map[string]*Block{
			"e": {Name: "e", Term: Jmp, Succ: []string{"A"}},
			"A": {Name: "A", Term: Jmp, Succ: []string{"e"}},
			"B": {Name: "B", Term: Ret},
		}}
	bf := Flatten(bcfg, key)
	fmt.Printf("  order=%v fallback=0x%08x 末块case=0x%08x\n",
		bf.Order, bf.Fallback, bf.CaseOf[bf.Order[len(bf.Order)-1]])

	fmt.Println("== 5. 放弃条件 ==")
	inv := &CFG{Entry: "e", Order: []string{"e", "x"}, Blocks: map[string]*Block{
		"e": {Name: "e", Term: Inv, Succ: []string{"x"}},
		"x": {Name: "x", Term: Ret},
	}}
	fmt.Println("  含 invoke ->", Flatten(inv, key).Reason)
	one := &CFG{Entry: "e", Order: []string{"e"}, Blocks: map[string]*Block{
		"e": {Name: "e", Term: Ret},
	}}
	fmt.Println("  单块     ->", Flatten(one, key).Reason)
}
