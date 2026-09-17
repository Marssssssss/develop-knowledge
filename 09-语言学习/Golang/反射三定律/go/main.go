package main

// 反射三定律的 Go 版断言集（与 python/main.py 同一批场景）。
// 分组：A Kind/Type 基础 · B zero Value 不变量 · C 第一定律 · D 第二定律
//      E 第三定律（可设置性）· F 结构体字段与只读位的继承 · G panic 条件表

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

// mustPanic 断言必须 panic，且消息里含 needle。
func mustPanic(fn func(), needle, label string) {
	defer func() {
		r := recover()
		if r == nil {
			check(false, label, "本应 panic 但没 panic")
			return
		}
		msg := fmt.Sprint(r)
		check(indexOf(msg, needle) >= 0, label, "msg="+msg+" 缺 "+needle)
	}()
	fn()
}

func indexOf(s, sub string) int {
	for i := 0; i+len(sub) <= len(s); i++ {
		if s[i:i+len(sub)] == sub {
			return i
		}
	}
	return -1
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
