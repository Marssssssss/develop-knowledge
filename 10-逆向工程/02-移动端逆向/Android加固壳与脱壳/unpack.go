// unpack.go — 与 unpack.py 同语义的 Go 复刻(静态审查用)。
package main

import (
	"bytes"
	"crypto/sha1"
	"fmt"
	"hash/adler32"
)

var dexMagic = []byte("dex\n035\x00") // 8 字节

// dexHeaderOk:magic / checksum 范围 / signature 范围三段验收。
func dexHeaderOk(buf []byte) (bool, string) {
	if !bytes.Equal(buf[:8], dexMagic) {
		return false, "magic"
	}
	sum := adler32.Checksum(buf[12:])
	if uint64(sum) != uint64(uint32le(buf[8:12])) {
		return false, "checksum"
	}
	sig := sha1.Sum(buf[32:])
	if !bytes.Equal(sig[:], buf[12:32]) {
		return false, "signature"
	}
	return true, "ok"
}

func uint32le(b []byte) uint32 {
	return uint32(b[0]) | uint32(b[1])<<8 | uint32(b[2])<<16 | uint32(b[3])<<24
}

// Element / DexPathList / findClass 顺序语义。
type Element struct {
	Name    string
	Classes map[string]int
	Broken  bool
}

func (e *Element) FindClass(name string, suppressed *[]string) (string, bool) {
	if e.Broken {
		*suppressed = append(*suppressed, "IOException:"+e.Name)
		return "", false
	}
	if _, ok := e.Classes[name]; ok {
		return name, true
	}
	return "", false
}

type DexPathList struct {
	Elements   []Element
	Suppressed []string
}

func (p *DexPathList) FindClass(name string) (string, bool) {
	var supp []string
	for i := range p.Elements {
		if c, ok := p.Elements[i].FindClass(name, &supp); ok {
			return c, true
		}
	}
	p.Suppressed = append(p.Suppressed, supp...)
	return "", false
}

// AddDexPath 追加到尾部,原顺序不变。
func (p *DexPathList) AddDexPath(e Element) {
	p.Elements = append(p.Elements, e)
}

func main() {
	stub := Element{"classes.dex", map[string]int{"com.shell.Launcher": 1}, false}
	real := Element{"payload.dex", map[string]int{"com.real.MainActivity": 2}, false}
	pl := &DexPathList{Elements: []Element{stub}}

	if _, ok := pl.FindClass("com.real.MainActivity"); ok {
		fmt.Println("unexpected: payload visible before boot")
	}
	pl.AddDexPath(real)
	if c, ok := pl.FindClass("com.real.MainActivity"); ok {
		fmt.Println("after addDexPath:", c)
	}

	broken := &DexPathList{Elements: []Element{
		{"broken.dex", nil, true}, stub}}
	broken.FindClass("com.nothing.X")
	fmt.Println("suppressed:", broken.Suppressed)
}
