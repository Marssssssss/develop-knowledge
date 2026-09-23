package main

import "fmt"

func main() {
	fmt.Println("== 1. 一份典型 prolog ==")
	prolog := &UnwindInfo{
		Version: 1, Flags: 0, PrologSize: 5,
		Codes: []UnwindCode{
			{Offset: 5, Op: UwopAllocSmall, Info: 7},
			{Offset: 1, Op: UwopPushNonvol, Info: 5},
		},
	}
	fmt.Printf("  raw=% x\n", prolog.ToBytes())
	back, err := ParseUnwindInfo(prolog.ToBytes())
	if err != nil {
		panic(err)
	}
	fmt.Printf("  version=%d flags=%d prolog=%d count=%d\n",
		back.Version, back.Flags, back.PrologSize, len(back.Codes))

	fmt.Println("== 2. rip 落在 prolog 的不同位置 ==")
	for _, rip := range []int{0, 1, 5} {
		m := NewMachine(0x1000, map[int]int{0x1000: 0xAABB, 0x1040: 0xCCDD})
		applied := UnwindProlog(m, prolog, rip)
		fmt.Printf("  rip+%d -> 槽 %v rsp=0x%x rbp=0x%x\n",
			rip, applied, m.Regs["RSP"], m.Regs["RBP"])
	}

	fmt.Println("== 3. 带帧指针 ==")
	fp := &UnwindInfo{
		Version: 1, PrologSize: 8, FrameRegister: 5, FrameOffset: 2,
		Codes: []UnwindCode{
			{Offset: 8, Op: UwopSetFpReg},
			{Offset: 4, Op: UwopAllocSmall, Info: 7},
			{Offset: 1, Op: UwopPushNonvol, Info: 5},
		},
	}
	m := NewMachine(0x1000, map[int]int{0x1080: 0x9999})
	m.Regs["RBP"] = 0x1060
	fpBase := m.Regs["RBP"] - 32
	UnwindFull(m, fp)
	fmt.Printf("  SET_FPREG: rsp = RBP-32 = 0x%x；整段撤销后 rsp=0x%x rbp=0x%x\n",
		fpBase, m.Regs["RSP"], m.Regs["RBP"])

	fmt.Println("== 4. 三种栈分配编码 ==")
	for _, c := range []struct {
		label string
		u     *UnwindInfo
	}{
		{"ALLOC_SMALL info=15", &UnwindInfo{Codes: []UnwindCode{{Op: UwopAllocSmall, Info: 15}}}},
		{"ALLOC_LARGE info=0", &UnwindInfo{Codes: []UnwindCode{
			{Op: UwopAllocLarge, Info: 0}, {Word: 100}}}},
		{"ALLOC_LARGE info=1", &UnwindInfo{Codes: []UnwindCode{
			{Op: UwopAllocLarge, Info: 1}, {Word: 0x1234}, {Word: 0x0001}}}},
	} {
		mm := NewMachine(0, nil)
		UnwindFull(mm, c.u)
		fmt.Printf("  %-22s -> %d 字节\n", c.label, mm.Regs["RSP"])
	}

	fmt.Println("== 5. chained 槽位 ==")
	for _, n := range []int{1, 3, 4, 5} {
		u := &UnwindInfo{Codes: make([]UnwindCode, n)}
		fmt.Printf("  CountOfCodes=%d -> chained 槽 %d\n", n, u.ChainedSlot())
	}

	leaf := NewMachine(0x1000, map[int]int{0x1000: 0xBEEF})
	UnwindLeaf(leaf)
	fmt.Printf("== 6. 叶函数回退: rip=0x%x rsp=0x%x\n", leaf.Rip, leaf.Regs["RSP"])
}
