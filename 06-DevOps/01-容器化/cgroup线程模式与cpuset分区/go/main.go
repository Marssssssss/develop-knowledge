// cgroup v2 线程模式与 cpuset 分区 —— Go 侧演示入口（与 python/main.py 同题）。
package main

import "fmt"

func main() {
	fmt.Println("== 1. cgroup.type 的四态 ==")
	root := NewCgroup("/", nil, nil, []int{0, 1, 2, 3, 4, 5, 6, 7})
	a := NewCgroup("A", root, nil, nil)
	b := NewCgroup("B", a, nil, nil)
	fmt.Println("   A 新建:", a.TypeStr())
	fmt.Println("   B 转 threaded:", b.MakeThreaded(), b.TypeStr())
	fmt.Println("   父 A 变成:", a.TypeStr())
	c := NewCgroup("C", b, nil, nil)
	fmt.Println("   B 之下新建的 C:", c.TypeStr())

	fmt.Println("\n== 2. 转 threaded 的两个条件 ==")
	r := NewCgroup("/", nil, nil, []int{0, 1, 2, 3, 4, 5, 6, 7})
	p := NewCgroup("P", r, nil, nil)
	p.Enable("memory")
	q := NewCgroup("Q", p, nil, nil)
	fmt.Println("   父开域控制器:", q.MakeThreaded())
	r2 := NewCgroup("/", nil, nil, []int{0, 1, 2, 3, 4, 5, 6, 7})
	p2 := NewCgroup("P", r2, nil, nil)
	k := NewCgroup("K", p2, nil, nil)
	k.Procs[9] = true
	q2 := NewCgroup("Q", p2, nil, nil)
	fmt.Println("   父有已填充 domain 子:", q2.MakeThreaded())

	fmt.Println("\n== 3. partition 的读值 ==")
	r3 := NewCgroup("/", nil, nil, []int{0, 1, 2, 3, 4, 5, 6, 7})
	x := NewCgroup("X", r3, []int{0, 1, 2, 3}, nil)
	y := NewCgroup("Y", r3, []int{4, 5, 6, 7}, nil)
	x.SetPartition(partRoot)
	y.SetPartition(isolated)
	fmt.Printf("   X=%-8s exclusive.effective=%v\n", x.ReadPartition(),
		keys(x.CpusExclusiveEffective()))
	fmt.Printf("   Y=%-8s exclusive.effective=%v\n", y.ReadPartition(),
		keys(y.CpusExclusiveEffective()))

	fmt.Println("\n== 4. 抢同一批 CPU 的兄弟 ==")
	r4 := NewCgroup("/", nil, nil, []int{0, 1, 2, 3})
	m := NewCgroup("M", r4, []int{0, 1}, nil)
	n := NewCgroup("N", r4, []int{2, 3}, nil)
	n.CpusExclusive = toSet([]int{0, 1})
	m.SetPartition(partRoot)
	n.SetPartition(partRoot)
	fmt.Printf("   M=%s\n   N=%s\n", m.ReadPartition(), n.ReadPartition())

	fmt.Println("\n== 5. 父转 member 连累子 local 分区 ==")
	r5 := NewCgroup("/", nil, nil, []int{0, 1, 2, 3})
	pp := NewCgroup("P", r5, []int{0, 1, 2, 3}, nil)
	pp.SetPartition(partRoot)
	c1 := NewCgroup("C1", pp, []int{0, 1}, nil)
	c1.SetPartition(partRoot)
	fmt.Println("   P=root   时 C1:", c1.ReadPartition())
	pp.SetPartition(member)
	fmt.Println("   P=member 时 C1:", c1.ReadPartition())
	pp.SetPartition(partRoot)
	fmt.Println("   P 恢复   后 C1:", c1.ReadPartition())
}
