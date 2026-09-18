// 字典（双表渐进式 rehash）实现，见 main.go 头部引用的 dict.c 口径。
package main

const (
	initialSize      = 4 // DICT_HT_INITIAL_SIZE（dict.h 未读，取 4 演示）
	forceResizeRatio = 4 // dict.c: dict_force_resize_ratio
)

type entry struct {
	key string
	val string
}

type dict struct {
	ht        [2][][]entry // 桶 = 条目切片（链地址法）
	sizeExp   [2]int       // 容量 = 1 << sizeExp；-1 表示未初始化
	used      [2]int
	rehashidx int
	paused    int
	steps     int
	log       [][2]int // (旧容量, 新容量)
}

func newDict() *dict { return &dict{sizeExp: [2]int{-1, -1}, rehashidx: -1} }

func (d *dict) size(i int) int {
	if d.sizeExp[i] < 0 {
		return 0
	}
	return 1 << d.sizeExp[i]
}

func (d *dict) total() int      { return d.used[0] + d.used[1] }
func (d *dict) rehashing() bool { return d.rehashidx != -1 }

func fnv(s string) int {
	h := 2166136261
	for i := 0; i < len(s); i++ {
		h ^= int(s[i])
		h *= 16777619
	}
	if h < 0 {
		h = -h
	}
	return h
}

func (d *dict) index(key string, i int) int { return fnv(key) & (d.size(i) - 1) }

func nextExp(size int) int {
	if size <= initialSize {
		return 2 // log2(4)
	}
	exp := 0
	for (1 << exp) < size {
		exp++
	}
	return exp
}

// resize 对应 _dictResize()：准备第二张表；ht[0] 为空时直接换过去（首次初始化不是真 rehash）。
func (d *dict) resize(size int) bool {
	if d.rehashing() {
		return false
	}
	exp := nextExp(size)
	if exp == d.sizeExp[0] {
		return false // "Rehashing to the same table size is not useful."
	}
	d.ht[1] = make([][]entry, 1<<exp)
	d.log = append(d.log, [2]int{d.size(0), 1 << exp})
	d.sizeExp[1] = exp
	d.used[1] = 0
	d.rehashidx = 0
	if d.sizeExp[0] < 0 || d.used[0] == 0 {
		d.swapAndFinish()
	}
	return true
}

func (d *dict) expandIfNeeded() string {
	if d.rehashing() {
		return "rehashing"
	}
	if d.size(0) == 0 {
		d.resize(initialSize)
		return "init"
	}
	if d.used[0] >= d.size(0) {
		d.resize(d.used[0] + 1)
		return "load_factor_1:1"
	}
	if d.used[0] >= forceResizeRatio*d.size(0) {
		d.resize(d.used[0] + 1)
		return "force_ratio_4x"
	}
	return "none"
}

// rehash 对应 dictRehash()：搬最多 n 个非空桶，最多探望 n*10 个空桶。
func (d *dict) rehash(n int) {
	if !d.rehashing() {
		return
	}
	emptyVisits := n * 10
	for n > 0 && d.used[0] != 0 {
		for d.rehashidx < d.size(0) && len(d.ht[0][d.rehashidx]) == 0 {
			d.rehashidx++
			emptyVisits--
			if emptyVisits == 0 {
				d.steps++
				return
			}
		}
		if d.rehashidx >= d.size(0) {
			break
		}
		d.moveBucket(d.rehashidx)
		d.rehashidx++
		d.steps++
		n--
	}
	d.checkCompleted()
}

func (d *dict) moveBucket(idx int) {
	for _, e := range d.ht[0][idx] {
		k := d.index(e.key, 1)
		d.ht[1][k] = append(d.ht[1][k], e)
		d.used[0]--
		d.used[1]++
	}
	d.ht[0][idx] = nil
}

func (d *dict) checkCompleted() bool {
	if d.used[0] != 0 {
		return false
	}
	d.swapAndFinish()
	return true
}

func (d *dict) swapAndFinish() {
	d.ht[0], d.ht[1] = d.ht[1], nil
	d.sizeExp[0], d.sizeExp[1] = d.sizeExp[1], -1
	d.used[0], d.used[1] = d.used[1], 0
	d.rehashidx = -1
}

func (d *dict) stepIfNeeded(idx int) {
	if !d.rehashing() || d.paused != 0 {
		return
	}
	if idx >= d.rehashidx && idx < d.size(0) && len(d.ht[0][idx]) > 0 {
		d.moveBucket(idx)
		d.steps++
		d.checkCompleted()
		return
	}
	d.rehash(1)
}

// find 同时遍历两张表，并跳过 table0 中已经迁移的桶。
func (d *dict) find(key string) (string, bool) {
	if d.total() == 0 {
		return "", false
	}
	tables := 1
	if d.rehashing() {
		tables = 2
	}
	for table := 0; table < tables; table++ {
		idx := d.index(key, table)
		if table == 0 && idx < d.rehashidx {
			continue
		}
		for _, e := range d.ht[table][idx] {
			if e.key == key {
				return e.val, true
			}
		}
	}
	return "", false
}

func (d *dict) insert(key, val string) {
	d.expandIfNeeded()
	idx := 0
	if d.size(0) > 0 {
		idx = d.index(key, 0)
	}
	d.stepIfNeeded(idx)
	tables := 1
	if d.rehashing() {
		tables = 2
	}
	for table := 0; table < tables; table++ {
		i := d.index(key, table)
		if table == 0 && i < d.rehashidx {
			continue
		}
		for j := range d.ht[table][i] {
			if d.ht[table][i][j].key == key {
				d.ht[table][i][j].val = val
				return
			}
		}
	}
	target := 0
	if d.rehashing() { // "the bucket is always returned in the context of the second (new) hash table"
		target = 1
	}
	i := d.index(key, target)
	d.ht[target][i] = append(d.ht[target][i], entry{key: key, val: val})
	d.used[target]++
}

