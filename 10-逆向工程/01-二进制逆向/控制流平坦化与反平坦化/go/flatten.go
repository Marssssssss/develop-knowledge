// Package main 复刻 OLLVM 的控制流平坦化（Flattening）pass 与 scramble32。
//
// 原文实读：
//
//	obfuscator-llvm/llvm-4.0 lib/Transforms/Obfuscation/Flattening.cpp
//	obfuscator-llvm/llvm-4.0 lib/Transforms/Obfuscation/Utils.cpp
//	obfuscator-llvm/llvm-4.0 lib/Transforms/Obfuscation/CryptoUtils.cpp
package main

const mask32 = 0xFFFFFFFF

// Xtime 是 GF(2^8) 乘 2，模 0x11B。
func Xtime(a int) int {
	a <<= 1
	if a&0x100 != 0 {
		a ^= 0x11B
	}
	return a & 0xFF
}

// Mul 是 GF(2^8) 乘法。
func Mul(a, b int) int {
	r := 0
	for i := 0; i < 8; i++ {
		if b&1 != 0 {
			r ^= a
		}
		a = Xtime(a)
		b >>= 1
	}
	return r & 0xFF
}

// GfInv 是 GF(2^8) 求逆。
func GfInv(a int) int {
	if a == 0 {
		return 0
	}
	for x := 1; x < 256; x++ {
		if Mul(a, x) == 1 {
			return x
		}
	}
	return 0
}

// BuildSbox 生成 AES S 盒：GF 逆 + 仿射变换。
func BuildSbox() [256]int {
	var s [256]int
	for x := 0; x < 256; x++ {
		inv := GfInv(x)
		v := inv
		for _, r := range []int{1, 2, 3, 4} {
			v ^= ((inv << r) | (inv >> (8 - r))) & 0xFF
		}
		s[x] = v ^ 0x63
	}
	return s
}

var sbox = BuildSbox()

// Rotr32 是 32 位循环右移。
func Rotr32(v, n int) int {
	return ((v >> n) | (v << (32 - n))) & mask32
}

// BuildTables 由 S 盒现算 TE0，TE1..TE3 是它的字节右旋。
func BuildTables() ([256]int, [256]int, [256]int, [256]int) {
	var te0, te1, te2, te3 [256]int
	for x := 0; x < 256; x++ {
		s := sbox[x]
		s2 := Xtime(s)
		s3 := s2 ^ s
		te0[x] = (s2 << 24) | (s << 16) | (s << 8) | s3
		te1[x] = Rotr32(te0[x], 8)
		te2[x] = Rotr32(te0[x], 16)
		te3[x] = Rotr32(te0[x], 24)
	}
	return te0, te1, te2, te3
}

var te0, te1, te2, te3 = BuildTables()

// Load32H 取 key 前 4 字节的大端值。
func Load32H(key []byte) int {
	return int(key[0])<<24 | int(key[1])<<16 | int(key[2])<<8 | int(key[3])
}

// Scramble32 是 CryptoUtils::scramble32：四轮 T 表混合后与 key 前 4 字节异或。
func Scramble32(value int, key []byte) int {
	k := make([]int, 16)
	for i := 0; i < 16; i++ {
		k[i] = int(key[i]) & 0xFF
	}
	a := te0[((value>>24)^k[0])&0xFF] ^ te1[((value>>16)^k[1])&0xFF] ^
		te2[((value>>8)^k[2])&0xFF] ^ te3[(value^k[3])&0xFF]
	b := te0[((a>>24)^k[4])&0xFF] ^ te1[((a>>16)^k[5])&0xFF] ^
		te2[((a>>8)^k[6])&0xFF] ^ te3[(a^k[7])&0xFF]
	a = te0[((b>>24)^k[8])&0xFF] ^ te1[((b>>16)^k[9])&0xFF] ^
		te2[((b>>8)^k[10])&0xFF] ^ te3[(b^k[11])&0xFF]
	b = te0[((a>>24)^k[12])&0xFF] ^ te1[((a>>16)^k[13])&0xFF] ^
		te2[((a>>8)^k[14])&0xFF] ^ te3[(a^k[15])&0xFF]
	return (Load32H(key) ^ b) & mask32
}

// ---------------------------------------------------------------- CFG

// Term 是块终结指令类型。
const (
	Ret = "ret"
	Br  = "br"
	Jmp = "jmp"
	Inv = "invoke"
)

// Block 是一个基本块。
type Block struct {
	Name string
	Term string
	Succ []string
	Cond string
	Phi  map[string]bool
}

// NSucc 是后继个数（ret 为 0）。
func (b *Block) NSucc() int {
	if b.Term == Ret {
		return 0
	}
	return len(b.Succ)
}

// CFG 是一张控制流图。Order 显式记块顺序：Go 的 map 遍历无序，
// 而 case 值按下标分配，顺序不对结果就全变。
type CFG struct {
	Entry  string
	Order  []string
	Blocks map[string]*Block
}

// Flattened 是平坦化的产物。
type Flattened struct {
	OK        bool
	Reason    string
	Order     []string
	CaseOf    map[string]int
	EntryName string
	Trans     map[string]Trans
	Fallback  int
	Initial   int
	Demoted   []string
}

// Trans 描述一个块对 switchVar 的更新。
type Trans struct {
	Kind      string // ret / jmp / select
	Value     int
	ValueFalse int
}

// SplitEntry 复刻「入口以条件分支/多后继结尾时切出一段」。
func SplitEntry(cfg *CFG) string {
	e := cfg.Blocks[cfg.Entry]
	if e.Term == Ret {
		return ""
	}
	if e.Term == Br || e.NSucc() > 1 {
		name := e.Name + ".first"
		cfg.Blocks[name] = &Block{Name: name, Term: e.Term, Succ: append([]string{}, e.Succ...), Cond: e.Cond}
		e.Term = Jmp
		e.Succ = []string{name}
		return name
	}
	return ""
}

// Flatten 复刻 Flattening::flatten 的决策过程。
func Flatten(cfg *CFG, key []byte) *Flattened {
	f := &Flattened{CaseOf: map[string]int{}, Trans: map[string]Trans{}}
	for _, b := range cfg.Blocks {
		if b.Term == Inv {
			f.Reason = "invoke"
			return f
		}
	}
	if len(cfg.Blocks) <= 1 {
		f.Reason = "single block"
		return f
	}
	for _, n := range cfg.Order {
		if n != cfg.Entry {
			f.Order = append(f.Order, n)
		}
	}
	// 源码遍历顺序即块顺序；切出来的首块插到最前
	if first := SplitEntry(cfg); first != "" {
		f.Order = append([]string{first}, f.Order...)
	}
	if len(f.Order) == 0 {
		f.Reason = "nothing to flatten"
		return f
	}
	for i, n := range f.Order {
		f.CaseOf[n] = Scramble32(i, key)
	}
	f.EntryName = cfg.Entry
	f.Initial = Scramble32(0, key)
	f.Fallback = Scramble32(len(f.Order)-1, key)

	for _, n := range f.Order {
		b := cfg.Blocks[n]
		switch {
		case b.NSucc() == 0:
			f.Trans[n] = Trans{Kind: Ret}
		case b.NSucc() == 1:
			v, ok := f.CaseOf[b.Succ[0]]
			if !ok {
				v = f.Fallback
			}
			f.Trans[n] = Trans{Kind: Jmp, Value: v}
		default:
			t, ok := f.CaseOf[b.Succ[0]]
			if !ok {
				t = f.Fallback
			}
			fl, ok := f.CaseOf[b.Succ[1]]
			if !ok {
				fl = f.Fallback
			}
			f.Trans[n] = Trans{Kind: "select", Value: t, ValueFalse: fl}
		}
	}
	// fixStack：phi 与逃逸寄存器被降级到栈
	for _, b := range cfg.Blocks {
		for k := range b.Phi {
			f.Demoted = append(f.Demoted, k)
		}
		b.Phi = map[string]bool{}
	}
	f.OK = true
	return f
}

// RunFlattened 按状态机执行，返回访问过的块名序列与结束方式。
func RunFlattened(cfg *CFG, f *Flattened, cond map[string]bool, limit int) ([]string, string) {
	dispatch := map[int]string{}
	for n, v := range f.CaseOf {
		dispatch[v] = n
	}
	state := f.Initial
	seen := []string{}
	for i := 0; i < limit; i++ {
		n, ok := dispatch[state]
		if !ok {
			return seen, "dead"
		}
		seen = append(seen, n)
		t := f.Trans[n]
		switch t.Kind {
		case Ret:
			return seen, "ret"
		case Jmp:
			state = t.Value
		default:
			if cond[cfg.Blocks[n].Cond] {
				state = t.Value
			} else {
				state = t.ValueFalse
			}
		}
	}
	return seen, "loop"
}
