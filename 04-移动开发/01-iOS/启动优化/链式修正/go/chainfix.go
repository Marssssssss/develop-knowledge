// dyld 链式修正的 Go 侧转写,对照 apple-oss-distributions/dyld@main:
//   include/mach-o/fixup-chains.h   —— 位域与常量
//   mach_o/ChainedFixups.cpp        —— 遍历、解析、回写校验
package main

import "errors"

// ---- 常量 ----
const (
	startNone  = 0xFFFF // page_start[]:本页无修正
	startMulti = 0x8000 // page_start[]:本页多链起点(高位)
	startLast  = 0x8000 // overflow 列表最后一项(与 MULTI 同值)

	ptrArm64e         = 1
	ptr64             = 2
	ptr32             = 3
	ptr64Offset       = 6
	ptrArm64eKernel   = 7
	ptrArm64eUserland = 9

	importBasic   = 1
	importAddend  = 2
	importAddend64 = 3
)

var errBadAddend = errors.New("badAddend")
var errBadOrdinal = errors.New("badBindOrdinal")
var errBadDistance = errors.New("badChainDistance")
var errBadVmAddr = errors.New("badVmAddr")
var errBadVmOffset = errors.New("badVmOffset")
var errUnknownFormat = errors.New("unknown pointer_format")
var errNoContent = errors.New("no content")

// bits 取 [lo, hi] 闭区间的位,lo = 0 表示最低位(C 位域从 LSB 起分配)。
func bits(v uint64, lo, hi uint) uint64 {
	w := hi - lo + 1
	return (v >> lo) & (uint64(1)<<w - 1)
}

// ins 把 x 放进 [lo, hi] 位域,超出位宽的部分被静默截断,与 C 位域赋值一致。
func ins(v uint64, lo, hi, x uint) uint64 {
	w := hi - lo + 1
	m := uint64(1)<<w - 1
	return (v & ^(m << lo)) | ((x & m) << lo)
}

// signExtend 把 w 位宽的 v 按补码还原。
func signExtend(v uint64, w uint) int64 {
	if v&(uint64(1)<<(w-1)) != 0 {
		return int64(v) - int64(uint64(1)<<w)
	}
	return int64(v)
}

// Fixup 一个链式修正条目,Target 一律是未加 slide 的 vm offset。
type Fixup struct {
	IsBind       bool
	Authenticated bool
	Ordinal      uint64
	Addend       uint64
	Target       uint64
	Key          uint64
	AddrDiv      uint64
	Diversity    uint64
}

// Format 对应 ChainedFixups::PointerFormat。
type Format struct {
	Value     uint16
	Name      string
	Stride    uint64
	Is64      bool
	BindBits  uint
	NextBits  uint
	NextLo    uint
	UnauthVm  bool // true:unauth rebase 的 target 是 vmaddr(要减基址)
}

// MakeFormat 按 pointer_format 数值分派。
func MakeFormat(v uint16) (Format, error) {
	switch v {
	case ptr64:
		return Format{ptr64, "DYLD_CHAINED_PTR_64", 4, true, 24, 12, 51, true}, nil
	case ptr64Offset:
		return Format{ptr64Offset, "DYLD_CHAINED_PTR_64_OFFSET", 4, true, 24, 12, 51, false}, nil
	case ptr32:
		return Format{ptr32, "DYLD_CHAINED_PTR_32", 4, false, 20, 5, 26, true}, nil
	case ptrArm64e:
		return Format{ptrArm64e, "DYLD_CHAINED_PTR_ARM64E", 8, true, 16, 11, 51, true}, nil
	case ptrArm64eUserland:
		return Format{ptrArm64eUserland, "DYLD_CHAINED_PTR_ARM64E_USERLAND", 8, true, 16, 11, 51, false}, nil
	case ptrArm64eKernel:
		return Format{ptrArm64eKernel, "DYLD_CHAINED_PTR_ARM64E_KERNEL", 4, true, 16, 11, 51, false}, nil
	}
	return Format{}, errUnknownFormat
}

func (f Format) isArm64e() bool {
	return f.NextBits == 11
}

func (f Format) maxNext() uint64 {
	return f.Stride * (uint64(1)<<f.NextBits - 1)
}

func (f Format) is32() bool {
	return f.Value == ptr32
}

// NextLocation 返回下一个链节点地址;next == 0 表示链结束。
func (f Format) NextLocation(loc, raw uint64) (uint64, bool) {
	n := bits(raw, f.NextLo, f.NextLo+f.NextBits-1)
	if n == 0 {
		return 0, false
	}
	return loc + n*f.Stride, true
}

// Parse 把一个条目解析成 Fixup。
func (f Format) Parse(raw, pref uint64) Fixup {
	if f.is32() {
		raw &= 0xFFFFFFFF
		if bits(raw, 31, 31) != 0 {
			return Fixup{IsBind: true, Ordinal: bits(raw, 0, 19), Addend: bits(raw, 20, 25)}
		}
		return Fixup{Target: bits(raw, 0, 25) - pref}
	}
	if f.isArm64e() {
		switch {
		case bits(raw, 62, 62) != 0 && bits(raw, 63, 63) != 0:
			return Fixup{IsBind: true, Authenticated: true, Ordinal: bits(raw, 0, 15),
				Key: bits(raw, 49, 50), AddrDiv: bits(raw, 48, 48), Diversity: bits(raw, 32, 47)}
		case bits(raw, 62, 62) != 0:
			return Fixup{IsBind: true, Ordinal: bits(raw, 0, 15), Addend: bits(raw, 32, 50)}
		case bits(raw, 63, 63) != 0:
			return Fixup{Authenticated: true, Target: bits(raw, 0, 31),
				Key: bits(raw, 49, 50), AddrDiv: bits(raw, 48, 48), Diversity: bits(raw, 32, 47)}
		}
		t := bits(raw, 0, 42)
		hi := bits(raw, 43, 50)
		if f.UnauthVm {
			return Fixup{Target: (hi << 56) | (t - pref)}
		}
		return Fixup{Target: (hi << 56) | t}
	}
	if bits(raw, 63, 63) != 0 {
		return Fixup{IsBind: true, Ordinal: bits(raw, 0, 23), Addend: bits(raw, 24, 31)}
	}
	t := bits(raw, 0, 35)
	hi := bits(raw, 36, 43)
	if f.UnauthVm {
		return Fixup{Target: (hi << 56) | (t - pref)}
	}
	return Fixup{Target: (hi << 56) | t}
}

// Write 回写一个条目。源码先把值赋进位域(超宽被截断),再读回位域比对,
// 因此 next/addend/ordinal 超宽是在读回那一步被发现的。
func (f Format) Write(fx Fixup, delta, pref uint64) (uint64, error) {
	nxt := delta / f.Stride
	var raw uint64
	if f.is32() {
		if fx.IsBind {
			raw = ins(raw, 0, 19, fx.Ordinal)
			raw = ins(raw, 20, 25, fx.Addend)
			if bits(raw, 20, 25) != fx.Addend {
				return 0, errBadAddend
			}
			if bits(raw, 0, 19) != fx.Ordinal {
				return 0, errBadOrdinal
			}
		} else {
			want := fx.Target + pref
			raw = ins(raw, 0, 25, want)
			if bits(raw, 0, 25) != want {
				return 0, errBadVmOffset
			}
		}
		raw = ins(raw, 26, 30, nxt)
		if fx.IsBind {
			raw = ins(raw, 31, 31, 1)
		}
		if bits(raw, 26, 30)*f.Stride != delta {
			return 0, errBadDistance
		}
		return raw & 0xFFFFFFFF, nil
	}
	if fx.IsBind && !f.isArm64e() {
		raw = ins(raw, 0, 23, fx.Ordinal)
		raw = ins(raw, 24, 31, fx.Addend)
		if bits(raw, 24, 31) != fx.Addend {
			return 0, errBadAddend
		}
		if bits(raw, 0, 23) != fx.Ordinal {
			return 0, errBadOrdinal
		}
		raw = ins(raw, 63, 63, 1)
	} else if fx.IsBind {
		raw = ins(raw, 0, 15, fx.Ordinal)
		raw = ins(raw, 32, 50, fx.Addend)
		if bits(raw, 32, 50) != fx.Addend {
			return 0, errBadAddend
		}
		if bits(raw, 0, 15) != fx.Ordinal {
			return 0, errBadOrdinal
		}
		raw = ins(raw, 62, 62, 1)
	} else if f.isArm64e() {
		hi := (fx.Target >> 56) & 0xFF
		low := fx.Target & 0x00FFFFFFFFFFFFFF
		want := low
		if f.UnauthVm {
			want = low + pref
		}
		raw = ins(raw, 0, 42, want)
		raw = ins(raw, 43, 50, hi)
	} else {
		hi := (fx.Target >> 56) & 0xFF
		low := fx.Target & 0x00FFFFFFFFFFFFFF
		want := low
		if f.UnauthVm {
			want = low + pref
		}
		raw = ins(raw, 36, 43, hi)
		raw = ins(raw, 0, 35, want)
		if bits(raw, 0, 35) != want {
			return 0, errBadVmAddr
		}
	}
	raw = ins(raw, f.NextLo, f.NextLo+f.NextBits-1, nxt)
	if bits(raw, f.NextLo, f.NextLo+f.NextBits-1)*f.Stride != delta {
		return 0, errBadDistance
	}
	return raw, nil
}
