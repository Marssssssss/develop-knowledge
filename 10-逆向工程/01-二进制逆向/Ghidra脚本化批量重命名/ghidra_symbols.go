package main

// Ghidra 符号表语义的 Go 模型(与 ghidra_symbols.py 同题)。
//
// 依据(本轮实读的官方 javadoc / 文档):
//   * createLabel 三个重载与 createFunction / setEOLComment / removeSymbol 的签名见
//     FlatProgramAPI javadoc;类注释强调本类『NO METHODS SHOULD EVER BE REMOVED ...
//     Changing this class will break user scripts.』
//   * SourceType 优先级(官方 isHigherPriorityThan 说明):
//     「USER_DEFINED objects are higher priority than IMPORTED objects which are higher
//      priority than ANALYSIS objects which are higher priority than DEFAULT objects.」
//     文档另有直白结论:「Symbol source determines priority - user symbols won't be
//     overwritten by analysis.」本模型的核心规则就是这条。
//   * 命名规则与默认名前缀:「Start with letter or underscore / Contain letters, digits,
//     underscores / No spaces / Case-sensitive」、「Ghidra creates default symbols: FUN_
//     for functions, DAT_ for data, LAB_ for labels, SUB_ for subroutines」。
//   * 冲突用官方 makeUnique 口径:「if the name is a duplicate, the address will be
//     concatenated to name to make it unique」。
//   * 写操作必须包在事务里(startTransaction / endTransaction)。

import (
	"fmt"
	"regexp"
	"sort"
	"strings"
)

var sourcePriority = map[string]int{
	"DEFAULT": 0, "ANALYSIS": 1, "IMPORTED": 2, "USER_DEFINED": 3,
}

var defaultPrefixes = []string{"FUN_", "DAT_", "LAB_", "SUB_", "DWORD_", "UNK_", "thunk_FUN_"}

var nameRe = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*$`)

func isDefaultName(name string) bool {
	for _, p := range defaultPrefixes {
		if strings.HasPrefix(name, p) {
			return true
		}
	}
	return false
}

func checkName(name string) error {
	if !nameRe.MatchString(name) {
		return fmt.Errorf("非法符号名: %q(须以字母/下划线开头,只含字母数字下划线,无空格)", name)
	}
	return nil
}

func uniqueName(name string, addr uint64) string { return fmt.Sprintf("%s_%04x", name, addr) }

// ---------------------------------------------------------------- 数据结构

type Symbol struct {
	Addr    uint64
	Name    string
	Source  string
	Primary bool
	NS      string
}

type Func struct {
	Entry uint64
	Name  string
	EOL   string
}

type Program struct {
	Symbols     map[uint64][]Symbol
	Functions   map[uint64]*Func
	CreateCount int
}

func NewProgram() *Program {
	return &Program{Symbols: map[uint64][]Symbol{}, Functions: map[uint64]*Func{}}
}

func sortedAddr(m map[uint64][]Symbol) []uint64 {
	out := []uint64{}
	for k := range m {
		out = append(out, k)
	}
	sort.Slice(out, func(i, j int) bool { return out[i] < out[j] })
	return out
}

func sortedEntry(m map[uint64]*Func) []uint64 {
	out := []uint64{}
	for k := range m {
		out = append(out, k)
	}
	sort.Slice(out, func(i, j int) bool { return out[i] < out[j] })
	return out
}

// ---------------------------------------------------------------- 事务(快照式)

type snapshot struct {
	syms  []Symbol
	funcs []Func
	count int
}

func (p *Program) Dump() snapshot {
	syms := []Symbol{}
	for _, a := range sortedAddr(p.Symbols) {
		syms = append(syms, p.Symbols[a]...)
	}
	funcs := []Func{}
	for _, e := range sortedEntry(p.Functions) {
		funcs = append(funcs, *p.Functions[e])
	}
	return snapshot{syms, funcs, p.CreateCount}
}

func (p *Program) Load(s snapshot) {
	p.Symbols = map[uint64][]Symbol{}
	for _, sym := range s.syms {
		p.Symbols[sym.Addr] = append(p.Symbols[sym.Addr], sym)
	}
	p.Functions = map[uint64]*Func{}
	for i := range s.funcs {
		f := s.funcs[i]
		p.Functions[f.Entry] = &f
	}
	p.CreateCount = s.count
}

type Transaction struct {
	p      *Program
	name   string
	snap   snapshot
	active bool
}

func (p *Program) Begin(name string) *Transaction {
	return &Transaction{p: p, name: name, snap: p.Dump(), active: true}
}

func (t *Transaction) Commit() {
	if !t.active {
		panic("commit 前必须先 Begin")
	}
	t.active = false
}

func (t *Transaction) Rollback() {
	t.p.Load(t.snap)
	t.active = false
}

// ---------------------------------------------------------------- 符号操作

func (p *Program) SymbolsAt(addr uint64) []Symbol { return p.Symbols[addr] }

func (p *Program) PrimarySymbol(addr uint64) *Symbol {
	for i := range p.Symbols[addr] {
		if p.Symbols[addr][i].Primary {
			return &p.Symbols[addr][i]
		}
	}
	return nil
}

func (p *Program) AllSymbols() []Symbol {
	out := []Symbol{}
	for _, a := range sortedAddr(p.Symbols) {
		out = append(out, p.Symbols[a]...)
	}
	return out
}

func (p *Program) FunctionsList() []Func {
	out := []Func{}
	for _, e := range sortedEntry(p.Functions) {
		out = append(out, *p.Functions[e])
	}
	return out
}

func (p *Program) FunctionAt(addr uint64) *Func { return p.Functions[addr] }

// CreateLabel 对应 FlatProgramAPI.createLabel(addr, name, makePrimary, sourceType)。
func (p *Program) CreateLabel(addr uint64, name string, primary bool, source string) (*Symbol, error) {
	if err := checkName(name); err != nil {
		return nil, err
	}
	existing := p.Symbols[addr]
	for i := range existing {
		if existing[i].Name == name && existing[i].NS == "Global" {
			return &existing[i], nil // 同名幂等
		}
	}
	if primary {
		if prim := p.PrimarySymbol(addr); prim != nil &&
			sourcePriority[prim.Source] > sourcePriority[source] {
			return nil, fmt.Errorf("拒绝覆盖: 地址 %04x 的 primary 符号 %s 来源 %s 优先级高于 %s",
				addr, prim.Name, prim.Source, source)
		}
		for i := range existing {
			existing[i].Primary = false
		}
	}
	existing = append(existing, Symbol{addr, name, source, primary, "Global"})
	p.Symbols[addr] = existing
	p.CreateCount++
	return &p.Symbols[addr][len(existing)-1], nil
}

func (p *Program) CreateFunction(entry uint64, name string) (*Func, error) {
	if _, err := p.CreateLabel(entry, name, true, "USER_DEFINED"); err != nil {
		return nil, err
	}
	f := &Func{Entry: entry, Name: name}
	p.Functions[entry] = f
	return f, nil
}

// RenameSymbol:新建高优先级标签 + 删掉旧标签,并把函数名同步过去。
func (p *Program) RenameSymbol(addr uint64, oldName, newName, source string) error {
	if err := checkName(newName); err != nil {
		return err
	}
	found := false
	for _, s := range p.Symbols[addr] {
		if s.Name == oldName {
			found = true
		}
	}
	if !found {
		return fmt.Errorf("地址 %04x 上找不到符号 %s", addr, oldName)
	}
	if _, err := p.CreateLabel(addr, newName, true, source); err != nil {
		return err
	}
	kept := []Symbol{}
	for _, x := range p.Symbols[addr] {
		if x.Name != oldName {
			kept = append(kept, x)
		}
	}
	p.Symbols[addr] = kept
	if f, ok := p.Functions[addr]; ok {
		f.Name = newName
	}
	return nil
}

func (p *Program) SetEOLComment(addr uint64, comment string) bool {
	f, ok := p.Functions[addr]
	if !ok {
		return false
	}
	f.EOL = comment
	return true
}
