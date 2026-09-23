// Package elfhash 复刻 glibc 的 ELF 符号哈希查找：DT_HASH 与 DT_GNU_HASH。
//
// 原文实读：
//
//	elf/simple-dl-new-hash.h   GNU hash: h=5381; h=h*33+c
//	elf/simple-dl-hash.h       SysV hash: h=(h<<4)+c; hi=h&0xf0000000; ...
//	elf/dl-setup_hash.c        表头 4 字 + bitmask_nwords 必须是 2 的幂
//	elf/dl-lookup.c            bloom 过滤 + chain 扫描
//	sysdeps/generic/ldsodefs.h ELF_MACHINE_HASH_SYMIDX = hasharr - chain_zero
package main

import "errors"

const nativeClass = 64 // __ELF_NATIVE_CLASS (ELF64)
const classMask = nativeClass - 1

// DlElfHash 是 SysV DT_HASH 用的哈希，结果恒小于 2^28。
func DlElfHash(name string) uint32 {
	var h uint32
	for i := 0; i < len(name); i++ {
		h = (h << 4) + uint32(name[i])
		hi := h & 0xf0000000
		h ^= hi >> 24
		h &= 0x0fffffff
	}
	return h
}

// DlNewHash 是 GNU DT_GNU_HASH 用的哈希（djb2 变体，uint32 回绕）。
func DlNewHash(name string) uint32 {
	h := uint32(5381)
	for i := 0; i < len(name); i++ {
		h = h*33 + uint32(name[i])
	}
	return h
}

// GnuHashTable 按 linker 口径构造一张 .gnu.hash。
type GnuHashTable struct {
	Symbols   []string
	Symbias   int
	Nbuckets  int
	Nwords    int
	Idxbits   int
	Shift     uint
	Bitmask   []uint64
	Buckets   []uint32
	ChainZero []uint32
	Exported  []int
}

// NewGnuHashTable 依据 symbols 建表；symbias 之前的符号不进哈希表。
func NewGnuHashTable(symbols []string, symbias, nbuckets, nwords int, shift uint) (*GnuHashTable, error) {
	if nwords&(nwords-1) != 0 {
		return nil, errors.New("bitmask_nwords must be a power of two")
	}
	if nbuckets <= 0 || symbias < 0 || symbias > len(symbols) {
		return nil, errors.New("bad table geometry")
	}

	exp := make([]int, 0, len(symbols))
	for i := symbias; i < len(symbols); i++ {
		exp = append(exp, i)
	}
	// 按 bucket 排序，使同一 bucket 的符号在符号表里连成一段
	sortByBucket(exp, symbols, nbuckets)

	head := make([]string, symbias)
	copy(head, symbols[:symbias])
	syms := make([]string, 0, len(symbols))
	syms = append(syms, head...)
	for _, i := range exp {
		syms = append(syms, symbols[i])
	}
	exported := make([]int, 0, len(exp))
	for i := symbias; i < len(syms); i++ {
		exported = append(exported, i)
	}

	t := &GnuHashTable{
		Symbols:   syms,
		Symbias:   symbias,
		Nbuckets:  nbuckets,
		Nwords:    nwords,
		Idxbits:   nwords - 1,
		Shift:     shift,
		Bitmask:   make([]uint64, nwords),
		Buckets:   make([]uint32, nbuckets),
		ChainZero: make([]uint32, len(syms)),
		Exported:  exported,
	}

	for pos, i := range exported {
		h := DlNewHash(syms[i])
		b := h % uint32(nbuckets)
		if t.Buckets[b] == 0 {
			t.Buckets[b] = uint32(i)
		}
		last := true
		if pos+1 < len(exported) {
			nxt := exported[pos+1]
			if DlNewHash(syms[nxt])%uint32(nbuckets) == b {
				last = false
			}
		}
		t.ChainZero[i] = (h & ^uint32(1)) | boolToU32(last)
		w := (h / nativeClass) & uint32(t.Idxbits)
		t.Bitmask[w] |= 1 << (h & classMask)
		t.Bitmask[w] |= 1 << ((h >> shift) & classMask)
	}
	return t, nil
}

func boolToU32(b bool) uint32 {
	if b {
		return 1
	}
	return 0
}

func sortByBucket(idx []int, symbols []string, nbuckets int) {
	// 插入排序：链短且顺序稳定，便于与 Python 侧逐项对照
	for a := 1; a < len(idx); a++ {
		v := idx[a]
		vb := DlNewHash(symbols[v]) % uint32(nbuckets)
		b := a - 1
		for b >= 0 && DlNewHash(symbols[idx[b]])%uint32(nbuckets) > vb {
			idx[b+1] = idx[b]
			b--
		}
		idx[b+1] = v
	}
}

// BloomProbe 只做 bloom 一步，返回是否放行以及用到的字下标与两个比特位。
func (t *GnuHashTable) BloomProbe(h uint32) (bool, uint32, uint, uint) {
	widx := (h / nativeClass) & uint32(t.Idxbits)
	word := t.Bitmask[widx]
	b1 := uint(h & classMask)
	b2 := uint((h >> t.Shift) & classMask)
	hit := ((word>>b1)&(word>>b2))&1 == 1
	return hit, widx, b1, b2
}

// Lookup 复刻 do_lookup_x 的 GNU hash 分支，返回符号下标（未找到返回 -1）。
func (t *GnuHashTable) Lookup(name string) int {
	h := DlNewHash(name)
	if hit, _, _, _ := t.BloomProbe(h); !hit {
		return -1
	}
	bucket := t.Buckets[h%uint32(t.Nbuckets)]
	if bucket == 0 {
		return -1
	}
	i := int(bucket)
	for {
		if i < 0 || i >= len(t.ChainZero) {
			return -1
		}
		hv := t.ChainZero[i]
		if ((hv ^ h) >> 1) == 0 && t.Symbols[i] == name {
			return i
		}
		if hv&1 == 1 {
			return -1
		}
		i++
	}
}

// SymidxFromHasharr 是 ELF_MACHINE_HASH_SYMIDX 的默认实现语义。
func (t *GnuHashTable) SymidxFromHasharr(bucket, offset int) int {
	return bucket + offset
}

// Words32 序列化为 DT_GNU_HASH 指向的 uint32 数组。
func (t *GnuHashTable) Words32() []uint32 {
	w := []uint32{uint32(t.Nbuckets), uint32(t.Symbias), uint32(t.Nwords), uint32(t.Shift)}
	for _, v := range t.Bitmask {
		w = append(w, uint32(v), uint32(v>>32))
	}
	w = append(w, t.Buckets...)
	for i := t.Symbias; i < len(t.ChainZero); i++ {
		w = append(w, t.ChainZero[i])
	}
	return w
}

// ParsedGnuHash 是 _dl_setup_hash 解析出的各字段。
type ParsedGnuHash struct {
	Nbuckets  uint32
	Symbias   uint32
	Nwords    uint32
	Idxbits   uint32
	Shift     uint32
	Bitmask   []uint64
	Buckets   []uint32
	ChainZero []uint32
}

// ParseGnuHash 复刻 _dl_setup_hash 的 DT_GNU_HASH 分支。
func ParseGnuHash(w []uint32) (*ParsedGnuHash, error) {
	if len(w) < 4 {
		return nil, errors.New("truncated gnu hash header")
	}
	nwords := w[2]
	if nwords&(nwords-1) != 0 {
		return nil, errors.New("bitmask_nwords must be a power of two")
	}
	p := &ParsedGnuHash{
		Nbuckets: w[0], Symbias: w[1], Nwords: nwords,
		Idxbits: nwords - 1, Shift: w[3],
	}
	off := 4
	for i := uint32(0); i < nwords; i++ {
		if off+1 >= len(w) {
			return nil, errors.New("truncated bitmask")
		}
		p.Bitmask = append(p.Bitmask, uint64(w[off])|uint64(w[off+1])<<32)
		off += 2
	}
	if off+int(p.Nbuckets) > len(w) {
		return nil, errors.New("truncated buckets")
	}
	p.Buckets = append(p.Buckets, w[off:off+int(p.Nbuckets)]...)
	off += int(p.Nbuckets)
	p.ChainZero = make([]uint32, int(p.Symbias))
	p.ChainZero = append(p.ChainZero, w[off:]...)
	return p, nil
}

// SysvHashTable 是 DT_HASH：nbuckets / nchain / buckets[] / chain[]。
type SysvHashTable struct {
	Symbols  []string
	Symbias  int
	Nbuckets int
	Buckets  []uint32
	Chain    []uint32
}

// NewSysvHashTable 建表；STN_UNDEF(0) 同时是空桶与链表尾的哨兵。
func NewSysvHashTable(symbols []string, symbias, nbuckets int) *SysvHashTable {
	t := &SysvHashTable{
		Symbols: symbols, Symbias: symbias, Nbuckets: nbuckets,
		Buckets: make([]uint32, nbuckets),
		Chain:   make([]uint32, len(symbols)),
	}
	for i := len(symbols) - 1; i >= symbias; i-- {
		b := DlElfHash(symbols[i]) % uint32(nbuckets)
		t.Chain[i] = t.Buckets[b]
		t.Buckets[b] = uint32(i)
	}
	return t
}

// Lookup 走 SysV 的 bucket→chain 链表。
func (t *SysvHashTable) Lookup(name string) int {
	h := DlElfHash(name)
	y := t.Buckets[h%uint32(t.Nbuckets)]
	for y != 0 {
		if t.Symbols[y] == name {
			return int(y)
		}
		y = t.Chain[y]
	}
	return -1
}

// Words32 序列化为 DT_HASH 指向的 uint32 数组。
func (t *SysvHashTable) Words32() []uint32 {
	w := []uint32{uint32(t.Nbuckets), uint32(len(t.Symbols))}
	w = append(w, t.Buckets...)
	w = append(w, t.Chain...)
	return w
}
