package main

// main.go — 样例函数构造与自检入口

import (
	"fmt"
	"os"
)


func sumLoop() *Func {
	return NewFunc("B0",
		&Block{Name: "B0", Ops: []Op{
			{Code: "COPY", Out: ptr(Rv("t")), Ins: []Vn{Const(0)}},
			{Code: "COPY", Out: ptr(Rv("i")), Ins: []Vn{Const(0)}},
		}, Succ: []string{"B1"}},
		&Block{Name: "B1", Ops: []Op{
			{Code: "INT_LESS", Out: ptr(Rv("cc")), Ins: []Vn{Rv("i"), Rv("n")}},
			{Code: "CBRANCH", Ins: []Vn{Rv("cc")}},
		}, Succ: []string{"B2", "B3"}},
		&Block{Name: "B2", Ops: []Op{
			{Code: "INT_ADD", Out: ptr(Rv("t")), Ins: []Vn{Rv("t"), Rv("i")}},
			{Code: "INT_ADD", Out: ptr(Rv("i")), Ins: []Vn{Rv("i"), Const(1)}},
			{Code: "COPY", Out: ptr(Rv("dead")), Ins: []Vn{Rv("t")}},
			{Code: "STORE", Ins: []Vn{Rv("sp"), Const(8), Rv("t")}},
		}, Succ: []string{"B1"}},
		&Block{Name: "B3", Ops: []Op{
			{Code: "RETURN", Ins: []Vn{Rv("t")}},
		}},
	)
}

func ptr(v Vn) *Vn { return &v }

func main() {
	fails := 0
	report := func(ok bool, label, detail string) {
		state := "PASS"
		if !ok {
			state, fails = "FAIL", fails+1
		}
		if detail != "" {
			fmt.Printf("  [%s] %s  <- %s\n", state, label, detail)
		} else {
			fmt.Printf("  [%s] %s\n", state, label)
		}
	}

	f := sumLoop()
	report(join(f.RPO()) == "B0 B1 B3 B2", "逆后序 = B0 B1 B3 B2", join(f.RPO()))
	idom, _ := f.Idoms()
	report(idom["B2"] == "B1" && idom["B3"] == "B1", "idom[B2]=idom[B3]=B1", "")
	n := f.PlacePhis()
	report(n == 2, "插入 2 个 phi(i 与 t)", fmt.Sprint(n))
	f.Rename()
	f.Dump("循环函数 SSA 形式")
	problems := f.Verify()
	report(len(problems) == 0, "SSA 校验通过", fmt.Sprint(problems))
	if fails > 0 {
		fmt.Printf("\nSOME CHECKS FAILED (fails=%d)\n", fails)
		os.Exit(1)
	}
	fmt.Println("\nALL CHECKS PASSED")
}
