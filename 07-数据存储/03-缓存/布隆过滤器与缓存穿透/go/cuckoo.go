package main

// 布谷鸟过滤器常量（cuckoo.h / config.c）
const (
	CuckooNullFp       = 0
	CfMaxNumBuckets    = 0x00FFFFFFFFFFFFFF // 56 位
	AltHashMultiplier  = 0x5BD1E995
	CompactDeleteRatio = 0.10

	DefaultCfBucketSize      = 2
	DefaultCfInitialSize     = 1024
	DefaultCfMaxIterations   = 20
	DefaultCfExpansionFactor = 1
	DefaultCfMaxExpansions   = 32
)

// 插入返回值（cuckoo.h CuckooInsertStatus）
const (
	CuckooInserted = 1
	CuckooExists   = 0
	CuckooNoSpace  = -1
	CuckooOOM      = -2
)

// GetNextN2 对应 cuckoo.c:35：向上取到最近的 2 的幂（0 → 0，由调用方兜底为 1）。
func GetNextN2(n int) int {
	if n == 0 {
		return 0
	}
	n--
	for _, s := range []uint{1, 2, 4, 8, 16, 32} {
		n |= n >> s
	}
	return n + 1
}

// GetAltHash 对应 cuckoo.c:116：index ^ (fp * 0x5bd1e995)。
func GetAltHash(fp, index int) int {
	return index ^ (fp * AltHashMultiplier)
}

// LookupParams 对应 cuckoo.h 的 CuckooKey。
type LookupParams struct {
	I1 int
	I2 int
	Fp int
}

// GetLookupParams 对应 cuckoo.c:120：fp 落在 1..255（0 留作空槽标记）。
func GetLookupParams(h int) LookupParams {
	fp := h%255 + 1
	return LookupParams{I1: h, I2: GetAltHash(fp, h), Fp: fp}
}

// SubCF 对应 cuckoo.h 的 SubCF。
type SubCF struct {
	NumBuckets int
	BucketSize int
	Data       []int
}

// IndexOf 对应 cuckoo.c:130：取模而非按位与。
func (s *SubCF) IndexOf(h int) int {
	return (h % s.NumBuckets) * s.BucketSize
}

// CuckooFilter 对应 cuckoo.h 的 CuckooFilter。
type CuckooFilter struct {
	NumBuckets    int
	NumItems      int
	NumDeletes    int
	NumFilters    int
	BucketSize    int
	MaxIterations int
	Expansion     int
	Filters       []*SubCF
	Compacted     int
}

// NewCuckooFilter 对应 cuckoo.c:44 CuckooFilter_Init。
func NewCuckooFilter(capacity, bucketSize, maxIterations, expansion int) (*CuckooFilter, error) {
	if capacity < bucketSize*2 {
		return nil, errInvalid
	}
	cf := &CuckooFilter{
		NumBuckets: GetNextN2(capacity / bucketSize),
		BucketSize: bucketSize,
		MaxIterations: maxIterations,
		Expansion:     GetNextN2(expansion),
	}
	if cf.NumBuckets == 0 {
		cf.NumBuckets = 1
	}
	if cf.Grow() != 0 {
		return nil, errInvalid
	}
	return cf, nil
}

// Grow 对应 cuckoo.c:78：growth = expansion^numFilters，且总数不能突破 56 位。
func (cf *CuckooFilter) Grow() int {
	if cf.NumFilters == 0xFFFF {
		return -1
	}
	growth := 1
	for i := 0; i < cf.NumFilters; i++ {
		growth *= cf.Expansion
	}
	if growth > CfMaxNumBuckets/cf.NumBuckets {
		return -1
	}
	cf.Filters = append(cf.Filters, &SubCF{
		NumBuckets: cf.NumBuckets * growth,
		BucketSize: cf.BucketSize,
		Data:       make([]int, cf.NumBuckets*growth*cf.BucketSize),
	})
	cf.NumFilters++
	return 0
}

// Check 对应 cuckoo.c:171：两个候选桶任一命中即存在。
func (cf *CuckooFilter) Check(h int) bool {
	p := GetLookupParams(h)
	for _, sub := range cf.Filters {
		for _, hh := range []int{p.I1, p.I2} {
			base := sub.IndexOf(hh)
			for k := 0; k < sub.BucketSize; k++ {
				if sub.Data[base+k] == p.Fp {
					return true
				}
			}
		}
	}
	return false
}

// Count 对应 cuckoo.c:189：可能 > 1。
func (cf *CuckooFilter) Count(h int) int {
	p := GetLookupParams(h)
	total := 0
	for _, sub := range cf.Filters {
		for _, hh := range []int{p.I1, p.I2} {
			base := sub.IndexOf(hh)
			for k := 0; k < sub.BucketSize; k++ {
				if sub.Data[base+k] == p.Fp {
					total++
				}
			}
		}
	}
	return total
}

func (cf *CuckooFilter) findAvailable(sub *SubCF, p LookupParams) int {
	for _, hh := range []int{p.I1, p.I2} {
		base := sub.IndexOf(hh)
		for k := 0; k < sub.BucketSize; k++ {
			if sub.Data[base+k] == CuckooNullFp {
				return base + k
			}
		}
	}
	return -1
}

// koInsert 对应 cuckoo.c:292：踢出链，失败后按原路回滚。
func (cf *CuckooFilter) koInsert(sub *SubCF, h1, fp int) bool {
	victim := 0
	bucketIx := h1 % sub.NumBuckets
	counter := 0
	for counter < cf.MaxIterations {
		counter++
		base := bucketIx * sub.BucketSize
		sub.Data[base+victim], fp = fp, sub.Data[base+victim]
		bucketIx = GetAltHash(fp, bucketIx) % sub.NumBuckets
		altBase := bucketIx * sub.BucketSize
		empty := -1
		for k := 0; k < sub.BucketSize; k++ {
			if sub.Data[altBase+k] == CuckooNullFp {
				empty = altBase + k
				break
			}
		}
		if empty >= 0 {
			sub.Data[empty] = fp
			return true
		}
		victim = (victim + 1) % sub.BucketSize
	}
	counter = 0
	for counter < cf.MaxIterations {
		counter++
		victim = (victim + sub.BucketSize - 1) % sub.BucketSize
		bucketIx = GetAltHash(fp, bucketIx) % sub.NumBuckets
		base := bucketIx * sub.BucketSize
		sub.Data[base+victim], fp = fp, sub.Data[base+victim]
	}
	return false
}

// insertFP 对应 cuckoo.c:254：找空位 → 踢 → 扩容 → 重试。
func (cf *CuckooFilter) insertFP(p LookupParams) int {
	for i := cf.NumFilters - 1; i >= 0; i-- {
		if slot := cf.findAvailable(cf.Filters[i], p); slot >= 0 {
			cf.Filters[i].Data[slot] = p.Fp
			cf.NumItems++
			return CuckooInserted
		}
	}
	if cf.koInsert(cf.Filters[cf.NumFilters-1], p.I1, p.Fp) {
		cf.NumItems++
		return CuckooInserted
	}
	if cf.Expansion == 0 {
		return CuckooNoSpace
	}
	if cf.Grow() != 0 {
		return CuckooOOM
	}
	return cf.insertFP(p)
}

// Insert 对应 cuckoo.c:278：**不查重**。
func (cf *CuckooFilter) Insert(h int) int {
	return cf.insertFP(GetLookupParams(h))
}

// InsertUnique 对应 cuckoo.c:284：先查存在。
func (cf *CuckooFilter) InsertUnique(h int) int {
	p := GetLookupParams(h)
	if cf.Check(h) {
		return CuckooExists
	}
	return cf.insertFP(p)
}

// Delete 对应 cuckoo.c:199：从最新子过滤器往回删；删除量超过 10% 触发压缩。
func (cf *CuckooFilter) Delete(h int) bool {
	p := GetLookupParams(h)
	for i := cf.NumFilters; i > 0; i-- {
		sub := cf.Filters[i-1]
		for _, hh := range []int{p.I1, p.I2} {
			base := sub.IndexOf(hh)
			for k := 0; k < sub.BucketSize; k++ {
				if sub.Data[base+k] == p.Fp {
					sub.Data[base+k] = CuckooNullFp
					cf.NumItems--
					cf.NumDeletes++
					if cf.NumFilters > 1 &&
						float64(cf.NumDeletes) > float64(cf.NumItems)*CompactDeleteRatio {
						cf.Compacted++
					}
					return true
				}
			}
		}
	}
	return false
}
