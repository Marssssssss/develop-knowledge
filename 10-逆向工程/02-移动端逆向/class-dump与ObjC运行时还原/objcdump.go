// objcdump.go — 与 objcdump.py 同语义的 Go 复刻(本机无 Go 工具链,静态审查用)。
// 结构常量出处同 Python 侧:class-dump CDObjectiveC2Processor.m + objc4 objc-runtime-new.h。
package main

import (
	"encoding/binary"
	"fmt"
)

const (
	wordShift   = 3
	roMeta      = 1 << 0 // RO_META,objc-runtime-new.h:359
	swiftBit    = 0x1
	dataMask    = ^uint64(7)
	entsizeMask = ^uint32(3)
)

type Section struct{ Off, Size int }

type Image struct {
	buf      []byte
	sections map[string]Section
}

func (im *Image) Alloc(data []byte, align int) int {
	for len(im.buf)%align != 0 {
		im.buf = append(im.buf, 0)
	}
	off := len(im.buf)
	im.buf = append(im.buf, data...)
	return off
}

func (im *Image) AddSection(name string, data []byte) int {
	off := im.Alloc(data, 8)
	im.sections[name] = Section{off, len(data)}
	return off
}

func (im *Image) U32(off int) uint32  { return binary.LittleEndian.Uint32(im.buf[off:]) }
func (im *Image) U64(off int) uint64  { return binary.LittleEndian.Uint64(im.buf[off:]) }
func (im *Image) CStr(off uint64) string {
	start := int(off)
	end := start
	for im.buf[end] != 0 {
		end++
	}
	return string(im.buf[start:end])
}

type Method struct{ Name, Types string; Imp uint64 }
type Ivar struct {
	Name        string
	AlignRaw    uint32
	Alignment   uint32
	Size        uint32
}
type ObjCClass struct {
	Isa, Superclass uint64
	IsSwift         bool
	DataFlags       uint64
	IsMeta          bool
	Name            string
	Methods         []Method
}

// MethodList 镜像 class-dump 的解析:entsize & ^3 后按 entsize 步进。
func (im *Image) MethodList(off uint64) []Method {
	if off == 0 {
		return nil
	}
	entsize := im.U32(int(off)) & entsizeMask
	count := im.U32(int(off) + 4)
	out := make([]Method, 0, count)
	for i := uint32(0); i < count; i++ {
		e := int(off) + 8 + int(i*entsize)
		out = append(out, Method{
			Name:  im.CStr(im.U64(e)),
			Types: im.CStr(im.U64(e + 8)),
			Imp:   im.U64(e + 16),
		})
	}
	return out
}

// IvarList 的 alignment():raw == ^uint32(0) → 1<<wordShift,否则 1<<raw。
func (im *Image) IvarList(off uint64) []Ivar {
	if off == 0 {
		return nil
	}
	entsize, count := im.U32(int(off)), im.U32(int(off)+4)
	out := make([]Ivar, 0, count)
	for i := uint32(0); i < count; i++ {
		e := int(off) + 8 + int(i*entsize)
		raw := im.U32(e + 24)
		align := uint32(1) << raw
		if raw == ^uint32(0) {
			align = 1 << wordShift
		}
		out = append(out, Ivar{
			Name: im.CStr(im.U64(e + 8)), AlignRaw: raw, Alignment: align, Size: im.U32(e + 28)})
	}
	return out
}

// ClassAt 镜像 objc2Class 读取序:isa|superclass|cache|vtable|data(&^7,bit0=swift)。
func (im *Image) ClassAt(addr uint64) ObjCClass {
	value := im.U64(int(addr) + 32)
	ro := value & dataMask
	c := ObjCClass{
		Isa: im.U64(int(addr)), Superclass: im.U64(int(addr) + 8),
		IsSwift: value&swiftBit != 0, DataFlags: value & 7,
	}
	flags := im.U32(int(ro))
	c.IsMeta = flags&roMeta != 0
	c.Name = im.CStr(im.U64(int(ro) + 24))
	c.Methods = im.MethodList(im.U64(int(ro) + 32))
	return c
}

// LoadClasses 镜像 loadClasses:__objc_classlist 每 8 字节一个类指针。
func (im *Image) LoadClasses() []ObjCClass {
	sec := im.sections["__objc_classlist"]
	out := make([]ObjCClass, 0, sec.Size/8)
	for i := 0; i < sec.Size/8; i++ {
		out = append(out, im.ClassAt(im.U64(sec.Off+i*8)))
	}
	return out
}

func main() {
	im := &Image{sections: map[string]Section{}}
	// 最小镜像:一个类,一个方法,ivar 用 ~0 对齐特例(与 selfcheck_objcdump.py 同构)。
	cstr := func(x string) uint64 { return uint64(im.Alloc(append([]byte(x), 0), 1)) }
	nName, nDesc, nSig := cstr("Person"), cstr("description"), cstr("v16@0:8")
	ivAge, ivType := cstr("_age"), cstr("i")

	methods := im.Alloc(concat(
		leU32(24|1), leU32(1), leU64(nDesc), leU64(nSig), leU64(0x5000)), 8)
	ivRaw := append([]byte{}, leU64(0), leU64(ivAge), leU64(ivType)...)
	ivRaw = append(ivRaw, leU32(0xFFFFFFFF), leU32(4)...)
	ivars := im.Alloc(concat(leU32(32), leU32(1), ivRaw...), 8)
	roBody := concat(leU32(0), leU32(8), leU32(16), leU32(0),
		leU64(0), leU64(nName), leU64(methods), leU64(0), leU64(ivars), leU64(0), leU64(0))
	ro := im.Alloc(roBody, 8)
	cls := im.Alloc(concat(leU64(0), leU64(0), leU64(0), leU64(0),
		leU64(ro|0b101), leU64(0), leU64(0), leU64(0)), 8)
	im.AddSection("__objc_classlist", leU64(cls))

	for _, c := range im.LoadClasses() {
		fmt.Printf("class %s meta=%v swift=%v flags=%b methods=%v ivarAlign=%d\n",
			c.Name, c.IsMeta, c.IsSwift, c.DataFlags,
			len(c.Methods), im.IvarList(im.U64(int(ro)+48))[0].Alignment)
	}
}

// --- 小工具 ---
func leU32(v uint32) []byte { b := make([]byte, 4); binary.LittleEndian.PutUint32(b, v); return b }
func leU64(v uint64) []byte { b := make([]byte, 8); binary.LittleEndian.PutUint64(b, v); return b }
func concat(parts ...[]byte) []byte {
	var out []byte
	for _, p := range parts {
		out = append(out, p...)
	}
	return out
}
