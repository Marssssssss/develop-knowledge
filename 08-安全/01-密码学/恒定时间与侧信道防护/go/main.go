package main

import "fmt"

func main() {
	fmt.Println("1. 掩码原语")
	fmt.Printf("   MSB(0x80000000) = %#010x\n", MSB(0x80000000))
	fmt.Printf("   Lt(3,7) = %#010x   Lt(7,3) = %#x\n", Lt(3, 7), Lt(7, 3))
	fmt.Printf("   Select(全1, 0xAA, 0x55) = %#04x\n", Select(mask32, 0xAA, 0x55))
	fmt.Printf("   Select(0,   0xAA, 0x55) = %#04x\n", Select(0, 0xAA, 0x55))

	fmt.Println()
	fmt.Println("2. memcmp：朴素版泄漏首个差异位置")
	for _, pos := range []int{0, 4, 15} {
		x := make([]byte, 16)
		for i := range x {
			x[i] = 0x5A
		}
		y := append([]byte(nil), x...)
		y[pos] ^= 1
		o1, o2 := &Observer{}, &Observer{}
		NaiveMemcmp(o1, x, y)
		CTMemcmp(o2, x, y)
		fmt.Printf("   差异在字节 %-2d  朴素=%-3d  常量时间=%-3d\n",
			pos, o1.Steps+len(o1.Branches), o2.Steps+len(o2.Branches))
	}

	fmt.Println()
	fmt.Println("3. 表查找")
	table := make([]byte, 256)
	for i := range table {
		table[i] = byte(i * 7 % 256)
	}
	o := &Observer{}
	NaiveLookup(o, table, 200)
	fmt.Printf("   朴素访问序列 = %v\n", o.Addr)
	o = &Observer{}
	v := CTLookup(o, table, 200)
	fmt.Printf("   常量时间取值 = %d，访问 %d 次（与索引无关）\n", v, len(o.Addr))

	fmt.Println()
	fmt.Println("4. 模幂")
	for _, exp := range []uint32{0b1011, 0b1000} {
		o1, o2 := &Observer{}, &Observer{}
		r1, b1 := NaiveModexp(o1, 3, exp, 65537)
		r2, _ := LadderModexp(o2, 3, exp, 65537)
		ones := 0
		for _, b := range b1 {
			ones += b
		}
		fmt.Printf("   exp=%b  朴素步数=%d（1 的个数=%d）  阶梯步数=%d  结果 %d/%d\n",
			exp, o1.Steps, ones, o2.Steps, r1, r2)
	}

	fmt.Println()
	fmt.Println("5. PKCS#7 去填充")
	for _, n := range []int{1, 8, 16} {
		data := make([]byte, 16)
		for i := 0; i < n; i++ {
			data[16-n+i] = byte(n)
		}
		o := &Observer{}
		got := CTPaddingCheck(o, data)
		fmt.Printf("   填充 %-2d 字节  -> %d，观测=%d（与长度无关）\n", n, got, o.Steps)
	}
}
