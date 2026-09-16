package main

// 两组样本数据:
//   makeBinaries  —— A/B 两个版本,每一对都对应一种指纹的作用范围:
//                    改一个立即数 / 改名 + 改立即数 / 改指令数(字符串引用不变) /
//                    删一个函数 / 加一个函数。
//   drillDown     —— 专门给 drill down 用:两侧各有一个"twin"函数,指纹与另一个
//                    函数完全相同(两侧都歧义,任何指纹算法都不匹配),
//                    只有已匹配函数的**唯一 callee** 这条关系能把它认出来。

func mk(insns []string, blocks map[string][]string, calls ...string) Func {
	c := map[string]int{}
	for _, x := range calls {
		c[x] = 1
	}
	return Func{Insns: map[string][]string{"b0": insns}, Blocks: blocks,
		Strings: []string{}, Calls: c}
}

func withStrings(f Func, s ...string) Func {
	f.Strings = s
	return f
}

func makeBinaries() (map[string]Func, map[string]Func) {
	blk := map[string][]string{"b0": {}}
	a := map[string]Func{
		"main":      mk([]string{"stp", "mov", "bl", "ldr", "ret"}, map[string][]string{"b0": {"end"}, "end": {}}, "parse"),
		"parse":     mk([]string{"ldr", "cmp", "b.ne", "bl", "b"}, map[string][]string{"b0": {"b1", "end"}, "b1": {"end"}, "end": {}}, "check"),
		"check":     mk([]string{"ldr", "mov", "cmp", "mov", "ret"}, blk),
		"hash":      mk([]string{"ldr", "eor", "eor", "eor", "str", "ret"}, blk),
		"log":       mk([]string{"adrp", "add", "bl", "ret"}, blk),
		"helperAdd": mk([]string{"add", "ret"}, blk),
	}
	a["log"] = withStrings(a["log"], "invalid input")
	b := map[string]Func{
		"main":  mk([]string{"stp", "mov", "bl", "ldr", "ret"}, map[string][]string{"b0": {"end"}, "end": {}}, "parse"),
		"parse": mk([]string{"ldr", "cmp", "b.ne", "bl", "b"}, map[string][]string{"b0": {"b1", "end"}, "b1": {"end"}, "end": {}}, "check"),
	}
	chk := mk([]string{"ldr", "mov", "cmp", "mov", "ret"}, blk)
	chk.ImmChanged = true // 只改了一个立即数:字节哈希失配,助记符序列不变
	b["check"] = chk
	alias := mk([]string{"ldr", "eor", "eor", "eor", "str", "ret"}, blk)
	alias.ImmChanged = true // 改了立即数 + 函数改了名
	b["hashAlias"] = alias
	b["log"] = withStrings(mk([]string{"adrp", "adrp", "add", "bl", "ret"}, blk),
		"invalid input") // 指令数变了,但字符串引用不变
	b["validate"] = mk([]string{"ldr", "cbz", "ret"}, blk) // 新增函数
	return a, b
}

func drillDown() (map[string]Func, map[string]Func) {
	blk := map[string][]string{"b0": {}}
	da := map[string]Func{
		"entry":  mk([]string{"bl", "ret"}, blk, "worker"),
		"worker": mk([]string{"add", "mul", "ret"}, blk),
		"twin":   mk([]string{"add", "mul", "ret"}, blk),
	}
	db := map[string]Func{
		"entry":   mk([]string{"bl", "ret"}, blk, "worker2"),
		"worker2": mk([]string{"add", "mul", "ret"}, blk),
		"twin":    mk([]string{"add", "mul", "ret"}, blk),
	}
	return da, db
}
