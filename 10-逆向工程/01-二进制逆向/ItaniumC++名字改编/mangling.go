// Itanium C++ ABI 名字改编（mangling）最小解 mangler —— Go 版。
//
// 与 mangling.py 同一套文法（https://itanium-cxx-abi.github.io/cxx-abi/abi.html §5.1）：
//   <unscoped-name> ::= <unqualified-name> | St <unqualified-name>
//   <nested-name>   ::= N [<CV-qualifiers>] [<ref-qualifier>] <prefix> <unqualified-name> E
//   <CV-qualifiers> ::= [r] [V] [K]     // restrict, volatile, const
//   <ref-qualifier> ::= R | O           // & / &&
//   <substitution>  ::= S_ | S<seq-id>_ , 缩写 St/Sa/Sb/Ss/Si/So/Sd
//
// 文件划分：mangling_const.go 常量表 / mangling_type.go 类型解析 / 本文件 名字与入口。
// 运行：go run mangling.go mangling_type.go mangling_const.go
package main

import (
	"fmt"
	"strings"
)

// --- 名字 ------------------------------------------------------------

func (d *Demangler) unqualifiedName() string {
	// 长度前缀必须先于两位操作符码判定：否则 "2ne" 会被误当成 operator!=
	if c := d.peek(1); c >= "0" && c <= "9" {
		return d.sourceName()
	}
	code := d.peek(2)
	if op, ok := operators[code]; ok {
		d.take(2)
		return "operator" + op
	}
	fail("不认识的 <unqualified-name>: %q（位置 %d）", d.peek(1), d.i)
	return ""
}

func (d *Demangler) unscopedName() string {
	d.expect("S")
	if d.peek(1) == "t" {
		d.take(1)
		d.addSub("std")
		return d.addSub("std::" + d.unqualifiedName())
	}
	base := d.substitution()
	if d.peek(1) == "I" {
		return d.addSub(d.renderTemplate(base, d.templateArgs()))
	}
	return base
}

func (d *Demangler) name() string {
	switch d.peek(1) {
	case "N":
		return d.nestedName()
	case "S":
		return d.unscopedName()
	}
	return d.unqualifiedName()
}

func (d *Demangler) nestedName() string {
	d.expect("N")
	d.cv = d.cvQualifiers()
	d.ref = d.refQualifier()
	parts := []string{}
	for d.peek(1) != "E" {
		switch d.peek(1) {
		case "S":
			parts = append(parts, d.unscopedName())
		case "I":
			d.addSub(strings.Join(parts, "::"))
			parts[len(parts)-1] = d.renderTemplate(parts[len(parts)-1], d.templateArgs())
			d.addSub(strings.Join(parts, "::"))
			continue
		default:
			parts = append(parts, d.unqualifiedName())
		}
		// 只有 <prefix> 是替换候选；最后那个 unqualified-name 不是
		if d.peek(1) != "E" {
			d.addSub(strings.Join(parts, "::"))
		}
	}
	d.expect("E")
	return strings.Join(parts, "::")
}

// Demangle 把一个 Itanium mangled name 还原成人类可读形式。
func Demangle(mangled string) (out string, err error) {
	defer func() {
		if r := recover(); r != nil {
			if e, ok := r.(error); ok {
				err = e
			} else {
				err = fmt.Errorf("%v", r)
			}
		}
	}()
	if !strings.HasPrefix(mangled, "_Z") {
		return mangled, nil // extern "C" / 非 C++ 符号原样返回
	}
	d := &Demangler{s: mangled}
	d.take(2)
	base := d.name()
	if d.i >= len(d.s) {
		return base, nil // 数据符号：没有参数列表
	}
	params := ""
	if d.peek(1) == "v" && d.i+1 == len(d.s) {
		d.take(1)
	} else {
		got := []string{}
		for d.i < len(d.s) {
			got = append(got, d.parseType())
		}
		params = strings.Join(got, ", ")
	}
	tail := ""
	if d.cv != "" {
		tail += " " + d.cv
	}
	if d.ref != "" {
		tail += " " + d.ref
	}
	return base + "(" + params + ")" + tail, nil
}

func main() {
	cases := []string{
		"_Z3fooi", "_Z1fv", "_ZN3Foo3barEi", "_ZN1AplERK1A",
		"_ZNSt6vectorIiSaIiEE9push_backERKi", "_Z3addPi", "_Z3foocc",
		"_ZNK3Foo3barEv", "_Z3fooiPd", "_ZNSaIiE4sizeEv", "_ZSt9terminatev",
		"_Z1fRK1A", "_Z1fOi", "_ZNSt3_In4wardE",
		"_ZN1N1TIiiE2mfES0_IddE", "_Z2nei", "_ZNKR3Foo3barEv", "printf",
	}
	for _, c := range cases {
		got, err := Demangle(c)
		if err != nil {
			fmt.Printf("%-42s -> ERR %v\n", c, err)
			continue
		}
		fmt.Printf("%-42s -> %s\n", c, got)
	}
}

