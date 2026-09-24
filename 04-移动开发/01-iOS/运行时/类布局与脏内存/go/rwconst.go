package main

import "errors"

// ---- FAST_*：打包进 class_data_bits_t::bits 指针空余位的标志 ----

const (
	FastIsSwiftLegacy = uint64(1) << 0
	FastIsSwiftStable = uint64(1) << 1
	FastHasDefaultRR  = uint64(1) << 2

	FastDataMaskIphone = uint64(0x0F00007FFFFFFFF8)
	FastDataMaskOther  = uint64(0x0F007FFFFFFFFFF8)
	FastDataMask32     = uint64(0xFFFFFFFC)

	FastFlagsMask64 = uint64(0x0000000000000007)
	FastFlagsMask32 = uint64(0x00000003)

	FastIsRwPointer64 = uint64(0x8000000000000000)
	FastIsRwPointer32 = uint64(0)
)

// ---- RW_*：class_rw_t->flags ----

const (
	RwRealized  = uint32(1) << 31
	RwFuture    = uint32(1) << 30
	RwInitizing = uint32(1) << 28
	RwRealizing = uint32(1) << 19
	RoMeta      = uint32(1) << 0
)

// Arch 是一套目标 ABI 上的 FAST_* 位布局。
type Arch struct {
	Name      string
	Width     int
	Full      uint64
	DataMask  uint64
	FlagsMask uint64
	RwBit     uint64
	PacMask   uint64
}

func makeArch(name string, width int, dataMask uint64, flagsMask uint64, rwBit uint64) Arch {
	full := uint64(0xFFFFFFFFFFFFFFFF)
	if width == 32 {
		full = uint64(0xFFFFFFFF)
	}
	return Arch{
		Name:      name,
		Width:     width,
		Full:      full,
		DataMask:  dataMask,
		FlagsMask: flagsMask,
		RwBit:     rwBit,
		PacMask:   ^(dataMask | flagsMask | rwBit) & full,
	}
}

// LP64IPhone / LP64Other / ILP32 三套 ABI。
func LP64IPhone() Arch {
	return makeArch("LP64 iPhone device", 64, FastDataMaskIphone, FastFlagsMask64, FastIsRwPointer64)
}

func LP64Other() Arch {
	return makeArch("LP64 non-device", 64, FastDataMaskOther, FastFlagsMask64, FastIsRwPointer64)
}

func ILP32() Arch {
	return makeArch("ILP32", 32, FastDataMask32, FastFlagsMask32, FastIsRwPointer32)
}

func mix64(v uint64, disc uint64) uint64 {
	x := v*0x9E3779B97F4A7C15 + disc*0xC2B2AE3D27D4EB4F + 0x165667B19E3779F9
	x ^= x >> 30
	x *= 0xBF58476D1CE4E5B9
	x ^= x >> 27
	x *= 0x94D049BB133111EB
	x ^= x >> 31
	return x
}

// ErrBadSignature 模拟 ptrauth 验签失败（真实硬件上是 trap）。
var ErrBadSignature = errors.New("bad pointer signature")

// PtrAuth 提供 sign / auth / strip 三件套。
//
// 真实 arm64e 上 ptrauth_strip（xpacd）不校验也不区分密钥——objc4 正是靠这
// 一点在 class_data_bits_t::flags() 里「无条件用 RO 密钥 strip」。因此这里让
// pac() 只依赖（值, 判别子）而不依赖密钥，Strip() 不做任何校验。
type PtrAuth struct {
	arch Arch
	disc uint64
}

func newPtrAuth(arch Arch, disc uint64) PtrAuth {
	return PtrAuth{arch: arch, disc: disc & arch.Full}
}

func (p PtrAuth) pac(v uint64) uint64 {
	return mix64(v&p.arch.Full, p.disc) & p.arch.PacMask
}

func (p PtrAuth) Sign(v uint64) uint64 {
	return v | p.pac(v)
}

func (p PtrAuth) Auth(v uint64) (uint64, error) {
	raw := v & ^p.arch.PacMask & p.arch.Full
	if v&p.arch.PacMask != p.pac(raw) {
		return 0, ErrBadSignature
	}
	return raw, nil
}

func (p PtrAuth) Strip(v uint64) uint64 {
	return v & ^p.arch.PacMask & p.arch.Full
}
