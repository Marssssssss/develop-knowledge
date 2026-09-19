package main

import (
	"fmt"
	"strings"
)

// Itanium C++ ABI 名字改编（mangling）—— 取字符/限定符/类型 解析部分。
// 与 mangling.py 同一套文法（abi.html §5.1）。

type Demangler struct {
	s    string
	i    int
	subs []string
	cv   string // nested-name 上的成员函数 CV 限定
	ref  string // nested-name 上的 ref-qualifier
}

func fail(format string, args ...interface{}) {
	panic(fmt.Errorf(format, args...))
}

// --- 基础取字符 ------------------------------------------------------

func (d *Demangler) peek(n int) string {
	if d.i+n > len(d.s) {
		return d.s[d.i:]
	}
	return d.s[d.i : d.i+n]
}

func (d *Demangler) take(n int) string {
	v := d.peek(n)
	d.i += len(v)
	return v
}

func (d *Demangler) expect(ch string) {
	if d.peek(1) != ch {
		fail("期望 %q，实为 %q（位置 %d）", ch, d.peek(1), d.i)
	}
	d.i++
}

func (d *Demangler) digits() string {
	start := d.i
	for d.i < len(d.s) && d.s[d.i] >= '0' && d.s[d.i] <= '9' {
		d.i++
	}
	return d.s[start:d.i]
}

func (d *Demangler) sourceName() string {
	n := 0
	for _, c := range d.digits() {
		n = n*10 + int(c-'0')
	}
	return d.take(n)
}

func (d *Demangler) addSub(text string) string {
	if text == "" {
		return text
	}
	for _, old := range d.subs {
		if old == text {
			return text
		}
	}
	d.subs = append(d.subs, text)
	return text
}

// subID 解析 S<seq-id>_ 的下标：S_ 是第 0 项，S0_ 起为 base36 + 1。
func (d *Demangler) subID() int {
	if d.peek(1) == "_" {
		d.take(1)
		return 0
	}
	text := ""
	for d.peek(1) != "" && d.peek(1) != "_" {
		text += d.take(1)
	}
	d.expect("_")
	idx := 1
	for _, c := range text {
		if c >= '0' && c <= '9' {
			idx = idx*36 + int(c-'0')
		} else if c >= 'A' && c <= 'Z' {
			idx = idx*36 + int(c-'A') + 10
		}
	}
	return idx
}

func (d *Demangler) substitution() string {
	ch := d.peek(1)
	if _, ok := substAbbrev["S"+ch]; ok && ch != "" {
		d.take(1)
		return substAbbrev["S"+ch]
	}
	idx := d.subID()
	if idx >= len(d.subs) {
		fail("substitution 下标 %d 越界（表长 %d）", idx, len(d.subs))
	}
	return d.subs[idx]
}

// --- 限定符 ----------------------------------------------------------

func (d *Demangler) cvQualifiers() string {
	got := ""
	for _, q := range []string{"r", "V", "K"} {
		if d.peek(1) == q {
			d.take(1)
			got += q
		}
	}
	out := []string{}
	for _, pair := range cvOrder {
		if strings.IndexByte(got, pair.code) >= 0 {
			out = append(out, pair.word)
		}
	}
	return strings.Join(out, " ")
}

func (d *Demangler) refQualifier() string {
	switch d.peek(1) {
	case "R":
		d.take(1)
		return "&"
	case "O":
		d.take(1)
		return "&&"
	}
	return ""
}

// --- 类型 ------------------------------------------------------------

func (d *Demangler) parseType() string {
	if d.i >= len(d.s) {
		fail("类型解析到了字符串末尾")
	}
	ch := d.peek(1)
	switch ch {
	case "P":
		d.take(1)
		return d.addSub(d.parseType() + "*")
	case "R":
		d.take(1)
		return d.addSub(d.parseType() + "&")
	case "O":
		d.take(1)
		return d.addSub(d.parseType() + "&&")
	case "r", "V", "K":
		quals := d.cvQualifiers()
		base := d.parseType()
		if quals == "" {
			return base
		}
		return d.addSub(quals + " " + base)
	case "F":
		return d.functionType()
	case "S":
		d.take(1)
		base := d.substitution()
		if d.peek(1) == "I" {
			return d.addSub(d.renderTemplate(base, d.templateArgs()))
		}
		return base
	case "N":
		d.take(1)
		base := d.parseType()
		d.expect("E")
		return base
	}
	if name, ok := builtin[d.s[d.i]]; ok {
		d.take(1)
		return name // builtin 不进 substitution 表
	}
	return d.classEnumType()
}

func (d *Demangler) classEnumType() string {
	name := d.sourceName()
	full := d.addSub(name)
	if d.peek(1) == "I" {
		args := d.templateArgs()
		full = d.addSub(d.renderTemplate(full, args))
	}
	return full
}

func (d *Demangler) templateArgs() string {
	d.expect("I")
	args := []string{}
	for d.peek(1) != "E" {
		args = append(args, d.parseType())
	}
	d.expect("E")
	return strings.Join(args, ", ")
}

// renderTemplate 拼 name<args>；实参以 '>' 结尾时补空格，避免连成 ">>"。
func (d *Demangler) renderTemplate(name, args string) string {
	if strings.HasSuffix(args, ">") {
		return name + "<" + args + " >"
	}
	return name + "<" + args + ">"
}

func (d *Demangler) functionType() string {
	d.expect("F")
	ret := d.parseType()
	params := []string{}
	for d.peek(1) != "E" {
		params = append(params, d.parseType())
	}
	d.expect("E")
	return ret + "(" + strings.Join(params, ", ") + ")"
}

