package main

import "fmt"

var checks, failed int

func check(cond bool, label string) {
	checks++
	if !cond {
		failed++
		fmt.Println("FAIL:", label)
	}
}

func checkEq(got, want interface{}, label string) {
	checks++
	if fmt.Sprint(got) != fmt.Sprint(want) {
		failed++
		fmt.Printf("FAIL: %s (got=%v want=%v)\n", label, got, want)
	}
}

// official 构造规范 §Selectors 官方示例里的 T0/T1/T2/Q。
func official() *Universe {
	u := &Universe{Types: map[string]*GoType{}}
	u.Types["T0"] = &GoType{Name: "T0", Fields: map[string]string{"x": "int"},
		Methods: map[string]bool{"M0": true}} // func (*T0) M0()
	u.Types["T1"] = &GoType{Name: "T1", Fields: map[string]string{"y": "int"},
		Methods: map[string]bool{"M1": false}} // func (T1) M1()
	u.Types["T2"] = &GoType{Name: "T2", Fields: map[string]string{"z": "int"},
		Embedded: []Embed{{"T1", "T1", false}, {"T0", "T0", true}},
		Methods:  map[string]bool{"M2": true}}
	u.Types["Q"] = &GoType{Name: "Q", DefinedPtrOf: "T2"}
	return u
}

func main() {
	u := official()

	// 1) T 与 *T 的方法集
	checkEq(len(u.MethodSet("T0", false)), 0, "T0 的方法集为空（M0 是指针接收者）")
	checkEq(Names(u.MethodSet("T0", true)), []string{"M0"}, "*T0 的方法集 = {M0}")
	checkEq(Names(u.MethodSet("T1", false)), []string{"M1"}, "T1 的方法集 = {M1}")
	checkEq(Names(u.MethodSet("T1", true)), []string{"M1"}, "*T1 也含 M1")

	// 2) 提升：官方示例
	checkEq(Names(u.MethodSet("T2", false)), []string{"M0", "M1"}, "T2 = {M0, M1}")
	checkEq(Names(u.MethodSet("T2", true)), []string{"M0", "M1", "M2"}, "*T2 = {M0, M1, M2}")
	checkEq(u.MethodSet("T2", false)["M0"].Depth, 1, "M0 深度 1")
	checkEq(u.MethodSet("T2", false)["M1"].Depth, 1, "M1 深度 1")
	checkEq(u.MethodSet("T2", true)["M2"].Depth, 0, "M2 深度 0")

	// 3) 官方示例的选择器深度
	s, err := u.Lookup("T2", "z")
	check(err == nil, "t.z 合法")
	checkEq(s.Depth, 0, "t.z 深度 0")
	s, _ = u.Lookup("T2", "y")
	checkEq(s.Depth, 1, "t.y 深度 1")
	checkEq(fmt.Sprint(s.Path), "[T1]", "t.y 经 T1")
	s, _ = u.Lookup("T2", "x")
	checkEq(s.Depth, 1, "t.x 深度 1")
	checkEq(fmt.Sprint(s.Path), "[T0]", "t.x 经 T0")
	s, _ = u.Lookup("T2", "M0")
	checkEq(s.Kind, "method", "M0 是方法")
	checkEq(s.Depth, 1, "M0 深度 1")

	// 4) 定义型指针：字段放行，方法不放行
	_, err = u.Selector("Q", "x")
	check(err == nil, "q.x 合法（(*q).x 是字段）")
	_, err = u.Selector("Q", "M0")
	check(err != nil, "q.M0() 非法（(*q).M0 是方法）")
	_, err = u.Selector("Q", "z")
	check(err == nil, "q.z 合法")

	// 5) 嵌入值 vs 嵌入指针
	u2 := &Universe{Types: map[string]*GoType{}}
	u2.Types["A"] = &GoType{Name: "A", Methods: map[string]bool{"Val": false, "Ptr": true}}
	u2.Types["S1"] = &GoType{Name: "S1", Embedded: []Embed{{"A", "A", false}}}
	u2.Types["S2"] = &GoType{Name: "S2", Embedded: []Embed{{"A", "A", true}}}
	checkEq(Names(u2.MethodSet("S1", false)), []string{"Val"}, "嵌入 A：S 只拿到值接收者")
	checkEq(Names(u2.MethodSet("S1", true)), []string{"Ptr", "Val"}, "嵌入 A：*S 两种都拿")
	checkEq(Names(u2.MethodSet("S2", false)), []string{"Ptr", "Val"}, "嵌入 *A：S 两种都拿")

	// 6) 歧义与遮蔽
	u3 := &Universe{Types: map[string]*GoType{}}
	u3.Types["A"] = &GoType{Name: "A", Fields: map[string]string{"F": "int"},
		Methods: map[string]bool{"M": false}}
	u3.Types["B"] = &GoType{Name: "B", Fields: map[string]string{"F": "int"},
		Methods: map[string]bool{"M": false}}
	u3.Types["C"] = &GoType{Name: "C", Embedded: []Embed{{"A", "A", false}, {"B", "B", false}}}
	u3.Types["D"] = &GoType{Name: "D", Fields: map[string]string{"F": "string"},
		Embedded: []Embed{{"A", "A", false}, {"B", "B", false}}}
	_, err = u.Lookup("C", "F")
	check(err != nil, "两处同深度的 F 是歧义")
	_, err = u.Lookup("C", "M")
	check(err != nil, "两处同深度的 M 是歧义")
	s, err = u.Lookup("D", "F")
	check(err == nil, "自身声明的 F 不歧义")
	checkEq(s.Depth, 0, "自身声明遮蔽提升")

	// 7) 接口方法集是交集
	u4 := &Universe{Types: map[string]*GoType{}}
	u4.Types["IA"] = &GoType{Name: "IA", Methods: map[string]bool{"M": false, "N": false}}
	u4.Types["IB"] = &GoType{Name: "IB", Methods: map[string]bool{"N": false, "O": false}}
	u4.Types["I"] = &GoType{Name: "I", IsInterface: true, TypeSet: []string{"IA", "IB"}}
	checkEq(Names(u4.MethodSet("I", false)), []string{"N"}, "接口方法集 = 交集 {N}")
	_, err = u4.Lookup("I", "M")
	check(err != nil, "不在方法集里的 M 非法")

	// 8) 空白标识符
	_, err = u.Lookup("T2", "_")
	check(err != nil, "空白标识符不是合法选择器")

	fmt.Printf("checks=%d failed=%d\n", checks, failed)
	if failed > 0 {
		panic("selfcheck failed")
	}
}
