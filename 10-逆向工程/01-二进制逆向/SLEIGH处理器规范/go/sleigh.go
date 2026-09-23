// Package main 是 Ghidra SLEIGH 处理器规范语言的最小引擎。
//
// 原文实读：
//
//	GhidraDocs/languages/html/sleigh_definitions.html  endian/alignment/space/register/bitrange
//	GhidraDocs/languages/html/sleigh_tokens.html       define token、字段区间、signed/hex/dec、attach variables
//	GhidraDocs/languages/html/sleigh_constructors.html 构造函数五段、约束、& 与 |、操作数、... 与 ;
//	Ghidra/Processors/6502/data/languages/6502.slaspec 真实规范样例
package main

import "errors"

// Token 是一条 token 定义：位宽 + 若干字段区间。
type Token struct {
	Name   string
	Bits   int
	Endian string
	Fields map[string][2]int
	Attrs  map[string][]string
}

// NewToken 建一个 token。
func NewToken(name string, bits int, endian string) *Token {
	return &Token{Name: name, Bits: bits, Endian: endian,
		Fields: map[string][2]int{}, Attrs: map[string][]string{}}
}

// AddField 声明字段；(lo,hi) 闭区间，最低位为 0。
func (t *Token) AddField(name string, lo, hi int, attrs ...string) error {
	if hi < lo {
		return errors.New("字段区间必须是 (lo,hi)，最低位为 0")
	}
	if hi >= t.Bits {
		return errors.New("字段超出 token 位宽")
	}
	t.Fields[name] = [2]int{lo, hi}
	t.Attrs[name] = attrs
	return nil
}

// Value 按字节序把字节拼成整数。
func (t *Token) Value(raw []byte, endian string) int {
	order := t.Endian
	if order == "" {
		order = endian
	}
	v := 0
	if order == "big" {
		for _, b := range raw {
			v = v<<8 | int(b)
		}
		return v
	}
	for i := len(raw) - 1; i >= 0; i-- {
		v = v<<8 | int(raw[i])
	}
	return v
}

// FieldValue 取字段值，signed 时按补码解释。
func (t *Token) FieldValue(name string, tokval int) int {
	r := t.Fields[name]
	lo, hi := r[0], r[1]
	v := (tokval >> lo) & ((1 << (hi - lo + 1)) - 1)
	for _, a := range t.Attrs[name] {
		if a == "signed" {
			w := hi - lo + 1
			if v>>(w-1)&1 == 1 {
				v -= 1 << w
			}
		}
	}
	return v
}

// Display 默认按十六进制显示（dec 目前不受支持）。
func (t *Token) Display(name string, tokval int) string {
	v := t.FieldValue(name, tokval)
	if v < 0 {
		return "-0x" + itoa(-v)
	}
	return "0x" + itoa(v)
}

func itoa(v int) string {
	if v == 0 {
		return "0"
	}
	digits := "0123456789abcdef"
	out := ""
	for v > 0 {
		out = string(digits[v%16]) + out
		v /= 16
	}
	return out
}

// Pattern 是位模式 AST。
type Pattern struct {
	Kind  string // and / or / constraint / operand / ellipsis
	Left  *Pattern
	Right *Pattern
	Field string
	Value int
}

// EvalPattern 求值；operands 收集单独出现的标识符。
func EvalPattern(p *Pattern, fields map[string]int, tokens, tables map[string]bool, operands *[]string) bool {
	switch p.Kind {
	case "and":
		return EvalPattern(p.Left, fields, tokens, tables, operands) &&
			EvalPattern(p.Right, fields, tokens, tables, operands)
	case "or":
		return EvalPattern(p.Left, fields, tokens, tables, operands) ||
			EvalPattern(p.Right, fields, tokens, tables, operands)
	case "ellipsis":
		return true
	case "constraint":
		v, ok := fields[p.Field]
		return ok && v == p.Value
	case "operand":
		*operands = append(*operands, p.Field)
		_, isField := fields[p.Field]
		return isField || tokens[p.Field] || tables[p.Field]
	}
	return false
}

// HasEllipsis 判模式里有没有 `...`。
func HasEllipsis(p *Pattern) bool {
	if p == nil {
		return false
	}
	if p.Kind == "ellipsis" {
		return true
	}
	if p.Kind == "and" || p.Kind == "or" {
		return HasEllipsis(p.Left) || HasEllipsis(p.Right)
	}
	return false
}

// Constructor 是一个构造函数。
type Constructor struct {
	Table     string // "" 表示根指令表
	Mnemonic  string
	Operands  []string
	Pattern   *Pattern
	Semantics string
}

// Spec 是一份规范。
type Spec struct {
	Endian       string
	Alignment    int
	Spaces       map[string]bool
	Registers    map[string]int
	Tokens       map[string]*Token
	TokenOrder   []string
	Constructors []*Constructor
	Attach       map[string][]string
}

// NewSpec 建一份空规范。
func NewSpec() *Spec {
	return &Spec{
		Spaces:       map[string]bool{},
		Registers:    map[string]int{},
		Tokens:       map[string]*Token{},
		Attach:       map[string][]string{},
		Constructors: []*Constructor{},
	}
}

// DefineEndian 设置字节序（第一条定义）。
func (s *Spec) DefineEndian(e string) error {
	if s.Endian != "" {
		return errors.New("endianness 只能定义一次")
	}
	s.Endian = e
	return nil
}

// DefineToken 登记 token。
func (s *Spec) DefineToken(t *Token) error {
	if t.Bits%8 != 0 {
		return errors.New("token 位宽必须是 8 的倍数")
	}
	s.Tokens[t.Name] = t
	s.TokenOrder = append(s.TokenOrder, t.Name)
	return nil
}

// AttachVariables 让每个字段都变成同一张寄存器查表（两侧不要求等长）。
func (s *Spec) AttachVariables(fields, registers []string) error {
	for _, f := range fields {
		known := false
		for _, t := range s.Tokens {
			if _, ok := t.Fields[f]; ok {
				known = true
			}
		}
		if !known {
			return errors.New("attach 的字段未声明: " + f)
		}
		s.Attach[f] = registers
	}
	return nil
}

// Insn 是解码结果。
type Insn struct {
	Mnemonic string
	Operands []string
	Length   int
}

// Decode 从 raw[addr:] 解一条指令。
func (s *Spec) Decode(raw []byte) *Insn {
	if len(s.TokenOrder) == 0 {
		return nil
	}
	for _, c := range s.Constructors {
		if c.Table != "" {
			continue
		}
		ins := s.tryRoot(c, raw)
		if ins != nil {
			return ins
		}
	}
	return nil
}

func (s *Spec) tryRoot(c *Constructor, raw []byte) *Insn {
	tok := s.Tokens[s.TokenOrder[0]]
	n := tok.Bits / 8
	if len(raw) < n {
		return nil
	}
	tokval := tok.Value(raw[:n], s.Endian)
	fields := map[string]int{}
	for f := range tok.Fields {
		fields[f] = tok.FieldValue(f, tokval)
	}
	used := n
	pool := []*Token{tok}
	vals := []int{tokval}

	if HasEllipsis(c.Pattern) && len(s.TokenOrder) >= 2 {
		t2 := s.Tokens[s.TokenOrder[1]]
		n2 := t2.Bits / 8
		if len(raw) < used+n2 {
			return nil
		}
		v2 := t2.Value(raw[used:used+n2], s.Endian)
		for f := range t2.Fields {
			fields[f] = t2.FieldValue(f, v2)
		}
		used += n2
		pool = append(pool, t2)
		vals = append(vals, v2)
	}

	tokens := map[string]bool{}
	for k := range s.Tokens {
		tokens[k] = true
	}
	tables := map[string]bool{}
	for _, x := range s.Constructors {
		if x.Table != "" {
			tables[x.Table] = true
		}
	}
	operands := []string{}
	if !EvalPattern(c.Pattern, fields, tokens, tables, &operands) {
		return nil
	}
	out := []string{}
	for _, op := range c.Operands {
		placed := false
		for i, t := range pool {
			if _, ok := t.Fields[op]; ok {
				v := t.FieldValue(op, vals[i])
				if regs, ok := s.Attach[op]; ok && v >= 0 && v < len(regs) {
					out = append(out, regs[v])
				} else {
					out = append(out, t.Display(op, vals[i]))
				}
				placed = true
				break
			}
		}
		if !placed {
			out = append(out, op)
		}
	}
	return &Insn{Mnemonic: c.Mnemonic, Operands: out, Length: used}
}
