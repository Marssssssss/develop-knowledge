package main

import "errors"

// ---- 极简地址堆：只为让 flags(bits) 能「按偏移 0 读 uint32」 ----

var heap = map[uint64]interface{}{}
var addrs = map[interface{}]uint64{}
var cursor = uint64(0x1000)

func alloc(o interface{}) uint64 {
	if a, ok := addrs[o]; ok {
		return a
	}
	a := cursor
	cursor += 0x40
	heap[a] = o
	addrs[o] = a
	return a
}

func deref(addr uint64) interface{} {
	return heap[addr]
}

func flagsOf(o interface{}) uint32 {
	switch t := o.(type) {
	case *ClassRo:
		return t.FlagsField
	case *ClassRw:
		return t.FlagsField
	}
	return 0
}

// ClassDataBits 把 class_ro_t / class_rw_t 指针与 3 个 FAST 标志挤进同一个字。
type ClassDataBits struct {
	arch           Arch
	ptra           PtrAuth
	DisableEnforce bool
	Bits           uint64
	CasFailures    int
	CasAttempts    int
}

func newClassDataBits(arch Arch, disc uint64) *ClassDataBits {
	return &ClassDataBits{arch: arch, ptra: newPtrAuth(arch, disc)}
}

func (b *ClassDataBits) authOrStrip(v uint64) (uint64, error) {
	if b.DisableEnforce {
		return b.ptra.Strip(v), nil
	}
	return b.ptra.Auth(v)
}

// Flags 读静态函数 flags(bits)：strip 后 & FAST_DATA_MASK，按偏移 0 取 uint32。
// 源码注释明说 "This intentionally DOES NOT check the signatures"。
func (b *ClassDataBits) Flags(bits uint64) uint32 {
	s := b.ptra.Strip(bits)
	return flagsOf(deref(s & b.arch.DataMask))
}

func (b *ClassDataBits) HasRwPointer(bits uint64) bool {
	if b.arch.RwBit != 0 {
		return bits&b.arch.RwBit != 0
	}
	// 32 位没有 FAST_IS_RW_POINTER，退化为「非空且 flags 里有 RW_REALIZED」
	return bits != 0 && b.Flags(bits)&RwRealized != 0
}

func (b *ClassDataBits) Data() (interface{}, error) {
	if !b.HasRwPointer(b.Bits) {
		return nil, errors.New("data() requires has_rw_pointer()")
	}
	authed, err := b.authOrStrip(b.Bits)
	if err != nil {
		return nil, err
	}
	return deref(authed & b.arch.DataMask), nil
}

// SafeRo 只 load 一次 bits，然后一次性决定走哪条路。
func (b *ClassDataBits) SafeRo(authenticate bool) (interface{}, error) {
	bitsValue := b.Bits
	if b.HasRwPointer(bitsValue) {
		rw, err := b.Data()
		if err != nil {
			return nil, err
		}
		if r, ok := rw.(*ClassRw); ok {
			return r.Ro()
		}
		return nil, errors.New("rw pointer is not a class_rw_t")
	}
	if authenticate && !b.DisableEnforce {
		authed, err := b.ptra.Auth(bitsValue)
		if err != nil {
			return nil, err
		}
		return deref(authed & b.arch.DataMask), nil
	}
	return deref(b.ptra.Strip(bitsValue) & b.arch.DataMask), nil
}

func (b *ClassDataBits) cas(old uint64, newBits uint64) bool {
	b.CasAttempts++
	if b.CasFailures > 0 {
		b.CasFailures--
		b.Bits = old ^ b.arch.FlagsMask // 制造一次「被并发改写」
		return false
	}
	b.Bits = newBits
	return true
}

// SetData 合成 newBits = (authedBits & FAST_FLAGS_MASK) | newData | FAST_IS_RW_POINTER。
func (b *ClassDataBits) SetData(rw *ClassRw) error {
	if b.HasRwPointer(b.Bits) && rw.FlagsField&(RwRealizing|RwFuture) == 0 {
		return errors.New("setData over an existing rw pointer needs RW_REALIZING|RW_FUTURE")
	}
	local := b.Bits
	authed := uint64(0)
	if local != 0 {
		a, err := b.authOrStrip(local)
		if err != nil {
			return err
		}
		authed = a
	}
	nb := (authed & b.arch.FlagsMask) | alloc(rw) | b.arch.RwBit
	b.Bits = b.ptra.Sign(nb) // store release
	return nil
}

func (b *ClassDataBits) SetAndClearBits(setBits uint64, clearBits uint64) error {
	if !b.HasRwPointer(b.Bits) {
		return errors.New("setAndClearBits requires has_rw_pointer()")
	}
	if setBits&clearBits != 0 {
		return errors.New("set and clear must not overlap")
	}
	for {
		old := b.Bits
		authBits, err := b.authOrStrip(old)
		if err != nil {
			return err
		}
		nb := (authBits | setBits) & ^clearBits & b.arch.Full
		nb = b.ptra.Sign(nb)
		if b.cas(old, nb) {
			return nil
		}
	}
}

// CopyRWFrom 换判别子重新签名（auth_and_resign），store release。
func (b *ClassDataBits) CopyRWFrom(other *ClassDataBits) error {
	raw, err := other.ptra.Auth(other.Bits)
	if err != nil {
		return err
	}
	b.Bits = b.ptra.Sign(raw) & b.arch.Full
	return nil
}

func (b *ClassDataBits) CopyROFrom(other *ClassDataBits, authenticate bool) error {
	if b.Flags(b.Bits)&RwRealized != 0 {
		return errors.New("copyROFrom requires RW_REALIZED unset")
	}
	raw := other.Bits
	if authenticate {
		r, err := other.ptra.Auth(raw)
		if err != nil {
			return err
		}
		raw = r
	}
	b.Bits = b.ptra.Sign(raw)
	return nil
}
