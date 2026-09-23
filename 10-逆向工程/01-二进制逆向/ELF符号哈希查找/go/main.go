package main

import "fmt"

func main() {
	syms := []string{"", "printf", "malloc", "free", "memcpy", "strlen", "my_export"}

	fmt.Println("== 1. 两套哈希函数 ==")
	for _, n := range []string{"printf", "malloc", "free"} {
		fmt.Printf("  %-8s sysv=0x%07x  gnu=0x%08x\n", n, DlElfHash(n), DlNewHash(n))
	}

	fmt.Println("== 2. 建表（按 bucket 排序使 chain 连续）==")
	t, err := NewGnuHashTable(syms, 1, 4, 2, 6)
	if err != nil {
		panic(err)
	}
	fmt.Printf("  buckets=%v\n  symbols=%v\n  chainZero=%v\n",
		t.Buckets, t.Symbols, t.ChainZero)

	fmt.Println("== 3. 查 printf ==")
	if i := t.Lookup("printf"); i >= 0 {
		fmt.Printf("  symidx=%d name=%s\n", i, t.Symbols[i])
	} else {
		fmt.Println("  not found")
	}

	fmt.Println("== 4. bloom 放行不等于命中 ==")
	big, _ := NewGnuHashTable(bigSymbols(), 1, 8, 2, 6)
	for _, p := range []string{"q0", "nope", "sym7"} {
		h := DlNewHash(p)
		hit, widx, b1, b2 := big.BloomProbe(h)
		fmt.Printf("  %-5s h=0x%08x word=%d bits=(%d,%d) bloom=%v lookup=%d\n",
			p, h, widx, b1, b2, hit, big.Lookup(p))
	}

	fmt.Println("== 5. SysV DT_HASH ==")
	s := NewSysvHashTable(syms, 1, 4)
	fmt.Printf("  buckets=%v chain=%v\n", s.Buckets, s.Chain)
	fmt.Printf("  free -> %d\n", s.Lookup("free"))

	fmt.Println("== 6. 解析口径 ==")
	p, err := ParseGnuHash(t.Words32())
	if err != nil {
		panic(err)
	}
	fmt.Printf("  idxbits=%d chainZeroLen=%d symidx(bucket0)=%d\n",
		p.Idxbits, len(p.ChainZero), t.SymidxFromHasharr(int(t.Buckets[0]), 0))

	if _, err := ParseGnuHash([]uint32{4, 1, 3, 6, 0, 0, 0, 0, 0, 0}); err == nil {
		panic("nwords=3 应当报错")
	} else {
		fmt.Println("  nwords 非 2 的幂 ->", err)
	}
}

func bigSymbols() []string {
	out := []string{""}
	for i := 0; i < 60; i++ {
		out = append(out, fmt.Sprintf("sym%d", i))
	}
	return out
}
