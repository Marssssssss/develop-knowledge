// deviation.go — 与 deviation.py 同语义的 Go 复刻(无本机工具链,静态审查用)。
package main

import "fmt"

const (
	accNative              = 0x100
	accBridge              = 0x40
	accSynthetic           = 0x1000
	accConstructor         = 0x10000
	accDeclaredSynchronized = 0x20000
	fallbackOpcode         = 0xff
)

type DexMethod struct {
	Name  string
	Access int
	Ops   []int
	Line  int // 0 = debug info 已剥离
}

type DexClass struct {
	Name    string
	Methods []DexMethod
}

type flagPair struct{ bit int; label string }

var flagNames = []flagPair{
	{accSynthetic, "synthetic"},
	{accBridge, "bridge"},
	{accNative, "native"},
	{accConstructor, "constructor"},
	{accDeclaredSynchronized, "declared-synchronized"},
}

// smali 视图:结构无损,全部标志保留。
func smaliFlags(m DexMethod) []string {
	var out []string
	for _, f := range flagNames {
		if m.Access&f.bit != 0 {
			out = append(out, f.label)
		}
	}
	return out
}

// jadx 视图:synthetic/bridge 转注释;失败块显式留痕;行号近似。
func javaNotes(m DexMethod) []string {
	var notes []string
	if m.Access&accSynthetic != 0 {
		notes = append(notes, "synthetic")
	}
	if m.Access&accBridge != 0 {
		notes = append(notes, "bridge")
	}
	for _, op := range m.Ops {
		if op == fallbackOpcode {
			notes = append(notes, "jadx: fallback")
			break
		}
	}
	return notes
}

func main() {
	cls := DexClass{"com.a.Main", []DexMethod{
		{"access$000", accSynthetic | 0x8, []int{0x12, 0x22}, 9},
		{"run", accBridge | accSynthetic, []int{0x12}, 0},
		{"weird", 0x1, []int{0x12, 0xff, 0x0a}, 30},
	}}
	for _, m := range cls.Methods {
		fmt.Printf("%s smali=%v java=%v line=%d\n",
			m.Name, smaliFlags(m), javaNotes(m), m.Line)
	}
}
