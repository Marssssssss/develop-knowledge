// Package main 复刻 RedisBloom 的布隆过滤器：位宽/哈希数公式、scalable 链的误差收紧与扩容。
package main

import "math"

// bloom.h 的四个选项
const (
	BloomOptNoRound    = 1 // 不取整到 2 的幂，省内存
	BloomOptEntsIsBits = 2 // entries 参数其实是位数的 log2
	BloomOptForce64    = 4 // 强制 64 位哈希
	BloomOptNoScaling  = 8 // 禁止扩容
)

// 常量与默认值（sb.c / config.c / config.h）
const (
	Ln2Squared              = 0.480453013918201 // bloom.c:121 的 denom
	ErrorTighteningRatio    = 0.5
	BfErrorRateCap          = 0.25
	DefaultBfErrorRate      = 0.01
	DefaultBfInitialSize    = 100
	DefaultBfExpansionFacto = 2

	ModeRead = 0
	ModeWrite = 1
)

const uint64Max = ^uint64(0)

// CalcBpe 对应 bloom.c:120：bpe = -ln(error)/ln(2)^2。
func CalcBpe(err float64) float64 {
	return -math.Log(err) / Ln2Squared
}

// Bloom 对应 deps/bloom 的 struct bloom。
type Bloom struct {
	Hashes  int
	Bits    int
	Bytes   int
	Bpe     float64
	Error   float64
	N2      int
	Entries int
	Bf      []byte
}

// NewBloom 对应 bloom.c:135 bloom_init。
func NewBloom(entries int, err float64, options int) (*Bloom, error) {
	if entries < 1 || err <= 0 || err >= 1.0 {
		return nil, errInvalid
	}
	b := &Bloom{Error: err, Bpe: CalcBpe(err), Entries: entries}
	var bits int
	switch {
	case options&BloomOptEntsIsBits != 0:
		if entries > 64 {
			return nil, errInvalid
		}
		b.N2 = entries
		bits = 1 << uint(b.N2)
		b.Entries = int(float64(bits) / b.Bpe)
	case options&BloomOptNoRound != 0:
		bits = int(float64(entries) * b.Bpe)
		if bits == 0 {
			bits = 1
		}
	default:
		product := float64(entries) * b.Bpe
		bn2 := math.Floor(math.Log2(product))
		if bn2 > 63 || math.IsInf(bn2, 0) {
			return nil, errInvalid
		}
		b.N2 = int(bn2) + 1
		bits = 1 << uint(b.N2)
		b.Entries = entries + int((float64(bits)-product)/b.Bpe)
	}
	// bytes 一律向上取到 8 的倍数（64 位字对齐）
	if bits%64 != 0 {
		b.Bytes = (bits/64 + 1) * 8
	} else {
		b.Bytes = bits / 8
	}
	b.Bits = b.Bytes * 8
	b.Hashes = int(math.Ceil(math.Ln2 * b.Bpe))
	b.Bf = make([]byte, b.Bytes)
	return b, nil
}

// mod 对应 bloom_check_add64 的 1<<n2 与 bloom_check_add_compat 的 bits。
func (b *Bloom) mod() int {
	if b.N2 > 0 {
		return 1 << uint(b.N2)
	}
	return b.Bits
}

func (b *Bloom) testBitSetBit(index, mode int) int {
	byteIdx, off := index>>3, index&7
	mask := byte(1 << uint(off))
	if b.Bf[byteIdx]&mask != 0 {
		return 1
	}
	if mode == ModeWrite {
		b.Bf[byteIdx] |= mask
	}
	return 0
}

// checkAdd 对应 bloom.c:87 的 CHECK_ADD_FUNC 宏：双哈希 (a + i*b) % mod。
func (b *Bloom) checkAdd(a, bval, mode int) int {
	foundUnset := 0
	mod := b.mod()
	for i := 0; i < b.Hashes; i++ {
		x := ((a + i*bval) % mod) % b.Bits
		if b.testBitSetBit(x, mode) == 0 {
			if mode == ModeRead {
				return 0
			}
			foundUnset = 1
		}
	}
	if mode == ModeRead {
		return 1
	}
	return foundUnset
}

// Check 全部位都为 1 → 1（可能存在）。
func (b *Bloom) Check(a, bval int) int { return b.checkAdd(a, bval, ModeRead) }

// Add 返回 foundUnset：1 表示本次确实置上了新位。
func (b *Bloom) Add(a, bval int) int { return b.checkAdd(a, bval, ModeWrite) }

// Popcount 统计已置位的个数。
func (b *Bloom) Popcount() int {
	n := 0
	for _, v := range b.Bf {
		for v != 0 {
			n += int(v & 1)
			v >>= 1
		}
	}
	return n
}

// ------------------------------------------------------------ scalable 链

// SBLink 对应 sb.h 的 SBLink。
type SBLink struct {
	Inner *Bloom
	Size  int
}

// SBChain 对应 sb.h 的 SBChain。
type SBChain struct {
	Filters []*SBLink
	Size    int
	Options int
	Growth  int
}

func (c *SBChain) cur() *SBLink { return c.Filters[len(c.Filters)-1] }

// AddLink 对应 sb.c:31。
func (c *SBChain) AddLink(size int, errRate float64) {
	b, e := NewBloom(size, errRate, c.Options)
	if e != nil {
		return
	}
	c.Filters = append(c.Filters, &SBLink{Inner: b})
}

// NewChain 对应 sb.c:128：首链的 error 就已经乘过 tightening。
func NewChain(initsize int, errRate float64, options, growth int) (*SBChain, error) {
	if initsize == 0 || errRate == 0 || errRate >= 1 {
		return nil, errInvalid
	}
	tightening := ErrorTighteningRatio
	if options&BloomOptNoScaling != 0 {
		tightening = 1
	}
	c := &SBChain{Options: options, Growth: growth}
	c.AddLink(initsize, errRate*tightening)
	return c, nil
}

// Add 对应 sb.c:83：先在新旧所有链里查，命中返回 0；当前链满了才加新链。
func (c *SBChain) Add(a, b int) int {
	for i := len(c.Filters) - 1; i >= 0; i-- {
		if c.Filters[i].Inner.Check(a, b) == 1 {
			return 0
		}
	}
	cur := c.cur()
	if cur.Size >= cur.Inner.Entries {
		if c.Options&BloomOptNoScaling != 0 {
			return -2 // SB_FULL
		}
		if c.Growth == 0 || cur.Inner.Entries > int(uint64Max/uint64(c.Growth)) {
			return -1 // SB_ERR
		}
		c.AddLink(cur.Inner.Entries*c.Growth, cur.Inner.Error*ErrorTighteningRatio)
		cur = c.cur()
	}
	rv := 0
	if cur.Inner.Add(a, b) == 1 {
		rv = 1
		cur.Size++
		c.Size++
	}
	return rv
}

// Check 对应 SBChain_Check：任一链命中即认为见过。
func (c *SBChain) Check(a, b int) bool {
	for _, l := range c.Filters {
		if l.Inner.Check(a, b) == 1 {
			return true
		}
	}
	return false
}

// BfReserveValidate 对应 rebloom.c:137 的参数校验。
func BfReserveValidate(errRate float64, capacity, expansion int, nonscaling bool) (
	float64, int, int, int, error) {
	if !(errRate > 0 && errRate < 1) {
		return 0, 0, 0, 0, errInvalid
	}
	if errRate > BfErrorRateCap {
		errRate = BfErrorRateCap
	}
	if capacity < 1 || capacity > (1<<30) {
		return 0, 0, 0, 0, errInvalid
	}
	options := BloomOptForce64 | BloomOptNoRound
	if expansion < 0 {
		if nonscaling {
			options |= BloomOptNoScaling
		}
		expansion = DefaultBfExpansionFacto
	} else {
		if expansion == 0 {
			options |= BloomOptNoScaling
		} else if nonscaling {
			return 0, 0, 0, 0, errInvalid
		}
		if expansion > 32768 {
			return 0, 0, 0, 0, errInvalid
		}
	}
	return errRate, capacity, expansion, options, nil
}
