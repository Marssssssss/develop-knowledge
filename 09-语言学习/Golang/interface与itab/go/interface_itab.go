// interface_itab.go — 单文件串讲 Go 接口:方法集 / itable / 动态派发 / 类型断言
// Demo 2: interface 与 itab(rsc《Go Data Structures: Interfaces》的模型复刻)
// 涵盖 6 个示例:方法集 T vs *T / itable 生成与缓存 / 赋值期派发 / 类型断言与
// type switch / nil interface 与 typed-nil 陷阱 / 空接口 any
package main

import (
	"fmt"
	"sort"
)

// ---------- 1) 方法集:T 的方法集只含值接收者方法,*T 两者都有 ----------
// 规范:method set of T = receiver T 的方法;method set of *T = receiver T 或 *T 的方法
type Speaker interface {
	Speak() string
}

type Cat struct{ Name string }

// 值接收者:进入 T 与 *T 两个方法集
func (c Cat) Speak() string { return c.Name + ": meow" }

type Dog struct{ Name string }

// 指针接收者:只进入 *Dog 的方法集,Dog 值本身不实现 Speaker
func (d *Dog) Speak() string { return d.Name + ": woof" }

func demoMethodSet() {
	fmt.Println("[1] 方法集 T vs *T")
	var s Speaker
	s = Cat{"mimi"} // Cat 值可赋给 Speaker(值接收者在方法集内)
	fmt.Println("  Cat 值   ->", s.Speak())
	s = &Dog{"wang"} // 必须取地址:&Dog 才实现 Speaker
	fmt.Println("  &Dog 值 ->", s.Speak())
	// s = Dog{"wang"} // 编译错误:Dog does not implement Speaker
	// (Speak method has pointer receiver)
}

// ---------- 2) itable 模拟:元数据 + 函数指针表 ----------
// rsc 文章:interface value = 双字对 (itable 指针, data 指针);
// itable = 关于 (接口类型, 具体类型) 的元数据 + fun 函数指针列表。
// 运行时在"赋值进接口"那一刻计算(或命中缓存),调用点只做一次间接调用。

// typeDescriptor 模拟编译器为每个类型生成的"类型描述结构"(含方法表)
type typeDescriptor struct {
	name    string
	methods map[string]func(any) string // 方法名 -> 实现函数
}

// itable 对应一个 (接口类型, 具体类型) 配对,只含接口要求的方法
type itable struct {
	inter    string // 接口类型名(元数据)
	concrete string // 具体类型名(元数据)
	fun      map[string]func(any) string
}

// iface 模拟运行时非空接口:双字对 -> (tab, data)
type iface struct {
	tab  *itable
	data any
}

// itabCache 模拟运行时的 itable 全局缓存:算过一次就不再算
var itabCache = map[[2]string]*itable{}

// getItab 复刻 rsc 描述的生成算法:对两个方法表排序后同步遍历,
// 把 O(ni*nt) 的暴力搜索降到 O(ni+nt);生成后写入缓存。
func getItab(interName string, interMethods []string, d *typeDescriptor) (*itable, bool) {
	key := [2]string{interName, d.name}
	if tab, ok := itabCache[key]; ok {
		fmt.Printf("  [cache hit ] %s/%s\n", interName, d.name)
		return tab, true
	}
	fmt.Printf("  [build itab] %s/%s : ", interName, d.name)
	sorted := append([]string(nil), interMethods...)
	sort.Strings(sorted) // 排序两个方法表,同步走一遍
	found := map[string]func(any) string{}
	for _, m := range sorted {
		if f, ok := d.methods[m]; ok {
			found[m] = f // 在具体类型方法表里命中接口方法
		}
	}
	if len(found) != len(interMethods) {
		fmt.Println("MISS(方法集不满足,赋值失败)")
		return nil, false
	}
	fmt.Println("OK")
	tab := &itable{inter: interName, concrete: d.name, fun: found}
	itabCache[key] = tab
	return tab, true
}

// assign 模拟 `var s Speaker = x`:在赋值点计算/查找 itable 并缓存
func assign(interName string, interMethods []string, d *typeDescriptor, val any) (iface, bool) {
	tab, ok := getItab(interName, interMethods, d)
	if !ok {
		return iface{}, false
	}
	return iface{tab: tab, data: val}, true
}

// 调用点:编译器生成的代码等价于 s.tab->fun[m](s.data) —— 一次间接调用
func call(iv iface, method string) string {
	return iv.tab.fun[method](iv.data)
}

// 具体类型的"方法表"(模拟编译器产物;接收者统一收到单字 data)
var (
	catDesc = &typeDescriptor{"Cat", map[string]func(any) string{
		"Speak": func(v any) string { return v.(Cat).Name + ": meow" },
	}}
	dogDesc = &typeDescriptor{"*Dog", map[string]func(any) string{
		"Speak": func(v any) string { return v.(*Dog).Name + ": woof" },
	}}
)

func demoItable() {
	fmt.Println("\n[2] itable 生成 / 缓存 / 赋值期派发")
	speakerMethods := []string{"Speak"}
	iv1, _ := assign("Speaker", speakerMethods, catDesc, Cat{"mimi"})
	iv2, _ := assign("Speaker", speakerMethods, catDesc, Cat{"kitty"})
	// 第二次赋值不同值:itable 命中缓存,不再生成
	fmt.Println("  调用(仅间接跳转):", call(iv1, "Speak"), "/", call(iv2, "Speak"))
	if _, ok := assign("Speaker", speakerMethods, dogDesc, &Dog{"wang"}); ok {
		fmt.Println("  *Dog 满足 Speaker")
	}
}

// ---------- 3) 类型断言与 type switch ----------
type Stringer interface{ String() string }

func (c Cat) String() string { return "Cat(" + c.Name + ")" }

func demoAssertion() {
	fmt.Println("\n[3] 类型断言 / type switch")
	var v any = Cat{"mimi"}
	// comma-ok 形式:失败不 panic,ok=false
	if c, ok := v.(Cat); ok {
		fmt.Println("  v.(Cat) ok ->", c.Name)
	}
	if _, ok := v.(int); !ok {
		fmt.Println("  v.(int)  miss -> ok=false(不 panic)")
	}
	// 单值形式失败会 run-time panic,生产代码用 comma-ok 防御
	// c := v.(int) // panic: interface conversion: any is Cat, not int

	// 接口到接口的断言:运行时询问方法集
	if s, ok := v.(Stringer); ok {
		fmt.Println("  v.(Stringer) ok ->", s.String())
	}

	// type switch:比较的是动态类型;guard 只求值一次
	switch x := v.(type) {
	case int:
		fmt.Println("  int:", x)
	case Cat:
		fmt.Println("  Cat:", x.Name)
	default:
		fmt.Println("  other:", x)
	}
}

// ---------- 4) nil interface vs typed-nil 陷阱 ----------
type MyErr struct{ Code int }

func (e *MyErr) Error() string { return fmt.Sprintf("MyErr(%d)", e.Code) }

// 返回 error 的函数:返回的是 *MyErr 的 nil 指针,不是 nil 接口
func failSometimes(fail bool) error {
	var e *MyErr // e == nil (*MyErr 类型)
	if fail {
		e = &MyErr{42}
	}
	return e // 装箱:接口 = (动态类型 *MyErr, data=nil) => 接口 != nil!
}

func demoTypedNil() {
	fmt.Println("\n[4] nil interface 与 typed-nil")
	var e error
	fmt.Printf("  var e error           -> e == nil : %v\n", e == nil) // 纯 nil 接口
	e = failSometimes(false)
	fmt.Printf("  e = (*MyErr)(nil) 装箱 -> e == nil : %v(陷阱!)\n", e == nil)
	// 原因:接口双字对中第一字(类型/itable)非空,接口整体就非 nil
	// 规范原文:x = v 之后 x 的动态类型是 *T,值为 (*T)(nil)
	// 规避:显式 return nil,或函数返回具体类型
	if e != nil {
		fmt.Println("  调用方误判 err != nil,走了错误分支")
	}
}

// ---------- 5) 空接口 any:itable 退化为直接指向类型描述 ----------
func demoEmptyInterface() {
	fmt.Println("\n[5] 空接口 any / interface{}")
	var a any = 42
	fmt.Printf("  a = 42      -> 动态类型 %T,值 %v\n", a, a)
	a = "hello"
	fmt.Printf("  a = hello   -> 动态类型 %T\n", a)
	// 空接口无方法 => itable 无意义,rsc:可去掉 itable,第一字直接指 type
}

func main() {
	demoMethodSet()
	demoItable()
	demoAssertion()
	demoTypedNil()
	demoEmptyInterface()
}
