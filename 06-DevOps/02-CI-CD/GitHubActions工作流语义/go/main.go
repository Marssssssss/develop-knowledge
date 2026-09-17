// 自检入口: 与 python/gha_semantics.py 对应的关键断言子集(Go 侧)。
//
// 运行: go run .
package main

import (
	"fmt"
	"sort"
	"strings"
)

var (
	pass, fail int
	failed     []string
)

func check(label string, cond bool, detail string) {
	if cond {
		pass++
		return
	}
	fail++
	failed = append(failed, label+" "+detail)
	fmt.Printf("  FAIL %s %s\n", label, detail)
}

func keys(m map[string]string) string {
	var ks []string
	for k, v := range m {
		ks = append(ks, k+"="+v)
	}
	sort.Strings(ks)
	return strings.Join(ks, ",")
}

func sortedCombos(cs []map[string]string) []string {
	var out []string
	for _, c := range cs {
		out = append(out, keys(c))
	}
	sort.Strings(out)
	return out
}

func main() {
	fmt.Println("GitHub Actions 语义自检(Go 对照)")

	// ---------- 触发过滤 ----------
	base := Event{Name: "push", Ref: "main", RefType: "branch", Paths: []string{"src/a.py"}}
	r, _, _ := Evaluate("push", nil, base)
	check("事件无过滤器直接运行", r, "")

	p1 := &Filter{Branches: []string{"releases/**", "!releases/**-alpha"}}
	r, _, _ = Evaluate("push", p1, Event{Name: "push", Ref: "releases/1.0-alpha", RefType: "branch"})
	check("负向模式在正向之后 -> 排除", !r, "")
	r, _, _ = Evaluate("push", p1, Event{Name: "push", Ref: "releases/1.0", RefType: "branch"})
	check("正向命中且未负向 -> 保留", r, "")

	p2 := &Filter{Branches: []string{"!releases/**-alpha", "releases/**"}}
	r, _, _ = Evaluate("push", p2, Event{Name: "push", Ref: "releases/1.0-alpha", RefType: "branch"})
	check("顺序反转后重新包含", r, "")

	r, _, _ = Evaluate("push", &Filter{Branches: []string{"feat*"}},
		Event{Name: "push", Ref: "feat/x", RefType: "branch"})
	check("* 不跨 /", !r, "")
	r, _, _ = Evaluate("push", &Filter{Branches: []string{"feat/**"}},
		Event{Name: "push", Ref: "feat/x", RefType: "branch"})
	check("** 跨 /", r, "")
	r, _, _ = Evaluate("push", &Filter{Branches: []string{"v1.?"}},
		Event{Name: "push", Ref: "v1", RefType: "branch"})
	check("? = 前一个字符 0 次", r, "")
	r, _, _ = Evaluate("push", &Filter{Branches: []string{"v1.?"}},
		Event{Name: "push", Ref: "v1..", RefType: "branch"})
	check("? 不能匹配 2 次", !r, "")
	r, _, _ = Evaluate("push", &Filter{Branches: []string{"ab+"}},
		Event{Name: "push", Ref: "abbb", RefType: "branch"})
	check("+ = 前一个字符 1 次以上", r, "")
	r, _, _ = Evaluate("push", &Filter{Branches: []string{"ab+"}},
		Event{Name: "push", Ref: "a", RefType: "branch"})
	check("+ 至少 1 次", !r, "")

	r, _, _ = Evaluate("push", &Filter{Branches: []string{"main"}},
		Event{Name: "push", Ref: "v1.0", RefType: "tag"})
	check("只声明 branches -> 标签不触发", !r, "")
	r, _, _ = Evaluate("push", &Filter{Tags: []string{"v1.*"}},
		Event{Name: "push", Ref: "main", RefType: "branch"})
	check("只声明 tags -> 分支不触发", !r, "")
	r, _, _ = Evaluate("push", &Filter{},
		Event{Name: "push", Ref: "v9", RefType: "tag"})
	check("都不声明 -> 标签触发", r, "")

	r, _, _ = Evaluate("push", &Filter{PathsIgnore: []string{"docs/**"}},
		Event{Name: "push", Ref: "main", RefType: "branch", Paths: []string{"docs/a.md", "docs/b.md"}})
	check("paths-ignore 全部命中 -> 不运行", !r, "")
	r, _, _ = Evaluate("push", &Filter{PathsIgnore: []string{"docs/**"}},
		Event{Name: "push", Ref: "main", RefType: "branch", Paths: []string{"docs/a.md", "src/x.py"}})
	check("paths-ignore 有一条未命中 -> 运行", r, "")

	js := Event{Name: "push", Ref: "main", RefType: "branch", Paths: []string{"a.js"}}
	r, _, _ = Evaluate("push", &Filter{Paths: []string{"**.js"}}, js)
	check("paths 命中", r, "")
	r, _, _ = Evaluate("push", &Filter{Paths: []string{"**.py"}}, js)
	check("paths 未命中且未超 3000", !r, "")
	r, _, _ = Evaluate("push", &Filter{Paths: []string{"**.py"}},
		Event{Name: "push", Ref: "main", RefType: "branch", Paths: []string{"a.js"}, FileCount: 3001})
	check("文件数 > 3000 保守运行", r, "")
	r, _, _ = Evaluate("push", &Filter{Paths: []string{"**.py"}, Branches: []string{"main"}},
		Event{Name: "push", Ref: "main", RefType: "branch", Paths: []string{"a.js"}, CommitCount: 1001})
	check("提交数 > 1000 绕过路径过滤", r, "")

	_, _, err := Evaluate("push", &Filter{Branches: []string{"a"}, BranchesIgnore: []string{"b"}}, base)
	check("branches + branches-ignore 校验失败", err != nil, "")
	_, _, err = Evaluate("push", &Filter{Paths: []string{"a"}, PathsIgnore: []string{"b"}}, base)
	check("paths + paths-ignore 校验失败", err != nil, "")

	// ---------- matrix ----------
	m := Matrix{Axes: []Axis{{"a", []string{"1", "2"}}, {"b", []string{"3", "4"}}}}
	check("笛卡尔积 2x2", len(Expand(m)) == 4, "")

	ex := Matrix{
		Axes:    []Axis{{"os", []string{"linux", "win"}}, {"node", []string{"18", "20"}}},
		Exclude: []map[string]string{{"os": "linux", "node": "18"}},
	}
	got := Expand(ex)
	check("exclude 去掉 1 个 -> 3", len(got) == 3, fmt.Sprint(len(got)))
	stale := false
	for _, c := range got {
		if c["os"] == "linux" && c["node"] == "18" {
			stale = true
		}
	}
	check("exclude 命中的组合确实不在", !stale, "")

	inc := Matrix{
		Axes: []Axis{{"fruit", []string{"apple", "pear"}}, {"animal", []string{"cat", "dog"}}},
		Include: []map[string]string{
			{"color": "green"},
			{"color": "pink", "animal": "cat"},
			{"fruit": "apple", "shape": "circle"},
			{"fruit": "banana"},
			{"fruit": "banana", "animal": "cat"},
		},
	}
	combos := Expand(inc)
	check("官方 include 示例组合数 = 6", len(combos) == 6, fmt.Sprint(len(combos)))
	want := []string{
		"animal=cat,color=pink,fruit=apple,shape=circle",
		"animal=dog,color=green,fruit=apple,shape=circle",
		"animal=cat,color=pink,fruit=pear",
		"animal=dog,color=green,fruit=pear",
		"fruit=banana",
		"animal=cat,fruit=banana",
	}
	gotS, wantS := sortedCombos(combos), append([]string{}, want...)
	sort.Strings(wantS)
	same := len(gotS) == len(wantS)
	if same {
		for i := range gotS {
			if gotS[i] != wantS[i] {
				same = false
			}
		}
	}
	check("官方 include 示例组合集合一致", same, strings.Join(gotS, " | "))
	tailDetail := "组合数不足 6"
	if len(combos) == 6 {
		tailDetail = keys(combos[4]) + " || " + keys(combos[5])
	}
	check("include 新建组合排在原始组合之后",
		len(combos) == 6 && combos[4]["fruit"] == "banana" && combos[5]["animal"] == "cat",
		tailDetail)
	check("include: [{}] 无副作用",
		len(Expand(Matrix{Axes: []Axis{{"x", []string{"1", "2"}}},
			Include: []map[string]string{{}}})) == 2, "")

	// ---------- 表达式 ----------
	ctx := map[string]interface{}{
		"github":    map[string]interface{}{"ref": "refs/heads/main", "event_name": "push"},
		"matrix":    map[string]interface{}{"project": "foo", "config": "Debug"},
		"__status__": map[string]interface{}{"any_failed": false, "cancelled": false},
	}
	ev := func(src string) interface{} {
		v, e := Eval(src, ctx)
		if e != nil {
			return "ERR:" + e.Error()
		}
		return v
	}
	check("单引号转义", ev("'It''s open source!'") == "It's open source!", "")
	check("双引号报错", strings.HasPrefix(fmt.Sprint(ev("\"abc\"")), "ERR:"), "")
	check("十六进制字面量", ev("0xff") == float64(255), "")
	check("null 字面量", ev("null") == nil, "")
	check("字符串比较忽略大小写", ev("'ABC' == 'abc'") == true, "")
	check("'1' == 1", ev("'1' == 1") == true, "")
	check("null == 0", ev("null == 0") == true, "")
	check("'' == 0", ev("'' == 0") == true, "")
	check("true == 1", ev("true == 1") == true, "")
	check("'abc' == 0 为假", ev("'abc' == 0") == false, "")
	check("NaN 参与 < 恒假", ev("'abc' < 1") == false, "")
	check("'10' > 9 按数字比较", ev("'10' > 9") == true, "")
	check("|| 返回操作数本身", ev("'' || 'fallback'") == "fallback", "")
	check("|| 左侧为真短路", ev("github.ref || 'fallback'") == "refs/heads/main", "")
	check("&& 返回右值", ev("'a' && 'b'") == "b", "")
	check("&& 左侧为假返回左值", ev("false && 'b'") == false, "")
	check("contains 忽略大小写", ev("contains('Hello world', 'LLO')") == true, "")
	check("contains 数组元素",
		ev("contains(fromJSON('[\"push\",\"pull_request\"]'), github.event_name)") == true, "")
	check("startsWith", ev("startsWith('Hello world', 'He')") == true, "")
	check("endsWith", ev("endsWith('Hello world', 'ld')") == true, "")
	check("format 替换", ev("format('Hello {0} {1} {2}', 'Mona', 'the', 'Octocat')") ==
		"Hello Mona the Octocat", "")
	check("format 花括号转义",
		ev("format('{{Hello {0} {1} {2}!}}', 'Mona', 'the', 'Octocat')") ==
			"{Hello Mona the Octocat!}", "")
	check("join 默认逗号", ev("join(fromJSON('[\"a\",\"b\"]'))") == "a,b", "")
	check("fromJSON 对象取值", ev("fromJSON('{\"a\":1}').a") == float64(1), "")
	check("fromJSON 布尔", ev("fromJSON('false')") == false, "")
	check("数组下标", ev("fromJSON('[10,20,30]')[1]") == float64(20), "")
	check("越界下标返回 null", ev("fromJSON('[10]')[5]") == nil, "")
	check("对象缺属性返回 null", ev("github.nope") == nil, "")
	check("success()", ev("success()") == true, "")
	check("failure()", ev("failure()") == false, "")
	check("always()", ev("always()") == true, "")
	check("! 优先级高于比较", ev("!false == true") == true, "")
	check("未知函数报错", strings.HasPrefix(fmt.Sprint(ev("nosuch(1)")), "ERR:"), "")
	check("多余 token 报错", strings.HasPrefix(fmt.Sprint(ev("1 2")), "ERR:"), "")

	fmt.Printf("\n断言 %d 通过 / %d 失败\n", pass, fail)
	if fail > 0 {
		for _, f := range failed {
			fmt.Println("  - " + f)
		}
	}
}
