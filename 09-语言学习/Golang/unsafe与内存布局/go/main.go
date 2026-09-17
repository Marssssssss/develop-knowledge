package main

// unsafe 与内存布局的 Go 版断言框架（与 python/main.py 同一批场景）。

import "fmt"

var (
	fails []string
	total int
)

func check(cond bool, label string, detail ...interface{}) {
	total++
	if !cond {
		fails = append(fails, fmt.Sprintf("%s  ::  %v", label, detail))
	}
}

func eq(got, want interface{}, label string) {
	g := fmt.Sprintf("%#v", got)
	w := fmt.Sprintf("%#v", want)
	check(g == w, label, "got="+g+" want="+w)
}

// Arr / PtrTo / FT 是构造复杂类型的小工具。
func Arr(elemTag string, n int) *Typ {
	return &Typ{Tag: "array", Elem: &Typ{Tag: elemTag}, N: n}
}

func PtrTo(elemTag string) *Typ {
	return &Typ{Tag: "ptr", Elem: &Typ{Tag: elemTag}}
}

func FT(name string, t *Typ) TField { return TField{name, t} }

// names 取字段名序列，便于断言最优顺序。
func names(t *Typ, order []int) []string {
	out := make([]string, len(order))
	for i, idx := range order {
		out[i] = t.Fields[idx].Name
	}
	return out
}

func main() {
	groupA()
	groupB()
	groupC()
	groupD()
	groupE()
	groupF()
	groupG()
	fmt.Printf("断言：%d 项，失败 %d 项\n", total, len(fails))
	for _, f := range fails {
		fmt.Println("  FAIL", f)
	}
	if len(fails) > 0 {
		panic("断言失败")
	}
}
