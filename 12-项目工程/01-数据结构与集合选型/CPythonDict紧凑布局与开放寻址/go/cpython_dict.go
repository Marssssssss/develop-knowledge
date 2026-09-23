// Package main —— CPython dict 紧凑布局与开放寻址探测的 Go 侧同口径实现。
//
// 与 python/cpython_dict.py 一一对应，常量与递推式全部照抄
// Objects/dictobject.c 与 Include/internal/pycore_dict.h。
package main

const (
	perturbShift     = 5
	pyDictLogMinSize = 3
	pyDictMinSize    = 1 << pyDictLogMinSize

	dkixEmpty = -1
	dkixDummy = -2
)

// usableFraction 复刻 `#define USABLE_FRACTION(n) (((n) << 1)/3)`。
func usableFraction(n int) int { return (n << 1) / 3 }

// calculateLog2Keysize 复刻 `bit_length(minsize-1)`，并先 Py_MAX 到 PyDict_MINSIZE。
func calculateLog2Keysize(minsize int) int {
	if minsize < pyDictMinSize {
		minsize = pyDictMinSize
	}
	v := minsize - 1
	n := 0
	for v > 0 {
		n++
		v >>= 1
	}
	return n
}

// estimateLog2Keysize 是 USABLE_FRACTION 的反函数：build 一张装 n 个不用再扩的表。
func estimateLog2Keysize(n int) int { return calculateLog2Keysize((n*3+1)/2) }

// growthRate 复刻 `#define GROWTH_RATE(d) ((d)->ma_used*3)`。
func growthRate(used int) int { return used * 3 }

// getLog2Bytes 索引表总字节数的 log2，四档分界为 8 / 16 / 32。
func getLog2Bytes(log2Size int) int {
	switch {
	case log2Size < pyDictLogMinSize:
		panic("dk_size must be >= PyDict_MINSIZE")
	case log2Size < 8:
		return log2Size
	case log2Size < 16:
		return log2Size + 1
	case log2Size >= 32:
		return log2Size + 3
	default:
		return log2Size + 2
	}
}

// indexBytesPerSlot 每个索引槽占的字节数。
func indexBytesPerSlot(log2Size int) int {
	return 1 << (getLog2Bytes(log2Size) - log2Size)
}

// probeIndices 生成探测序列：起始 i = hash & mask，之后每轮
// `perturb >>= PERTURB_SHIFT; i = (i*5 + perturb + 1) & mask`。
// perturb 是 uint64，右移是逻辑右移（Go 的 uint64 天然如此）。
func probeIndices(hashValue uint64, dkSize, limit int) []int {
	mask := uint64(dkSize - 1)
	i := hashValue & mask
	perturb := hashValue
	out := make([]int, 0, limit)
	for n := 0; n < limit; n++ {
		out = append(out, int(i))
		perturb >>= perturbShift
		i = (i*5 + perturb + 1) & mask
	}
	return out
}

// compactDict 把四个计数器都显式建出来：dk_usable / dk_nentries / ma_used / resizes。
type compactDict struct {
	log2Size  int
	indices   []int
	entries   []*entry
	dkUsable  int
	dkNentry  int
	maUsed    int
	resizes   int
	unicodeOK bool // 对应 DICT_KEYS_UNICODE：只收字符串 key
}

type entry struct {
	key  string
	hash uint64
}

func newCompactDict(log2Size int) *compactDict {
	k := calculateLog2Keysize(1 << log2Size)
	d := &compactDict{
		log2Size:  k,
		indices:   make([]int, 1<<k),
		unicodeOK: true,
	}
	for i := range d.indices {
		d.indices[i] = dkixEmpty
	}
	d.dkUsable = usableFraction(d.dkSize())
	return d
}

func (d *compactDict) dkSize() int      { return 1 << d.log2Size }
func (d *compactDict) capacity() int    { return usableFraction(d.dkSize()) }
func (d *compactDict) indexBytes() int  { return indexBytesPerSlot(d.log2Size) }
func (d *compactDict) probeSeq(h uint64) []int {
	return probeIndices(h, d.dkSize(), d.dkSize())
}

func (d *compactDict) findEmptySlot(h uint64) int {
	for _, i := range d.probeSeq(h) {
		if d.indices[i] == dkixEmpty {
			return i
		}
	}
	panic("no empty slot")
}

func (d *compactDict) lookupSlot(h uint64) int {
	for _, i := range d.probeSeq(h) {
		ix := d.indices[i]
		if ix >= 0 {
			return ix
		}
		if ix == dkixEmpty {
			return dkixEmpty
		}
	}
	panic("no empty slot")
}

func (d *compactDict) buildIndices() {
	d.indices = make([]int, d.dkSize())
	for i := range d.indices {
		d.indices[i] = dkixEmpty
	}
	for ix, ep := range d.entries {
		if ep == nil {
			continue
		}
		for _, i := range d.probeSeq(ep.hash) {
			if d.indices[i] == dkixEmpty {
				d.indices[i] = ix
				break
			}
		}
	}
}

// resize 对应 dictresize：压实 entries + 重建索引 + 重排两个计数器。
func (d *compactDict) resize(minsize int) {
	alive := make([]*entry, 0, len(d.entries))
	for _, ep := range d.entries {
		if ep != nil {
			alive = append(alive, ep)
		}
	}
	d.log2Size = calculateLog2Keysize(minsize)
	d.entries = alive
	d.dkNentry = len(alive)
	d.dkUsable = usableFraction(d.dkSize()) - len(alive)
	d.buildIndices()
	d.resizes++
	if d.dkUsable < 0 {
		panic("new table must be large enough")
	}
}

// insert 对应 insert_combined_dict：先判 usable <= 0 扩容，再 find_empty_slot 落位。
func (d *compactDict) insert(key string, h uint64) {
	if d.dkUsable <= 0 {
		d.resize(growthRate(d.maUsed))
	}
	i := d.findEmptySlot(h)
	d.indices[i] = d.dkNentry
	d.entries = append(d.entries, &entry{key: key, hash: h})
	d.dkNentry++
	d.dkUsable--
	d.maUsed++
}

// lookup 返回 entry 下标，找不到返回 dkixEmpty。
func (d *compactDict) lookup(key string, h uint64) int {
	ix := d.lookupSlot(h)
	if ix >= 0 && d.entries[ix] != nil && d.entries[ix].key == key {
		return ix
	}
	return dkixEmpty
}

// delete 对应 delitem_common 的合并表分支：置 DUMMY、清 entry、used--，其余不动。
func (d *compactDict) delete(key string, h uint64) bool {
	for _, i := range d.probeSeq(h) {
		ix := d.indices[i]
		if ix >= 0 {
			if d.entries[ix] != nil && d.entries[ix].key == key {
				d.indices[i] = dkixDummy
				d.entries[ix] = nil
				d.maUsed--
				return true
			}
		} else if ix == dkixEmpty {
			return false
		}
	}
	return false
}

func (d *compactDict) iterationOrder() []string {
	out := make([]string, 0, len(d.entries))
	for _, ep := range d.entries {
		if ep != nil {
			out = append(out, ep.key)
		}
	}
	return out
}

func (d *compactDict) dummyCount() int {
	n := 0
	for _, x := range d.indices {
		if x == dkixDummy {
			n++
		}
	}
	return n
}
