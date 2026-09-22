// 覆盖率引导模糊测试（AFL 模型）：边覆盖位图、命中数分桶、新边判定。
//
// 依据 google/AFL 的官方源码与文档：
//   - config.h 的 MAP_SIZE_POW2 / MAP_SIZE / CAL_CYCLES / TMOUT_LIMIT /
//     HAVOC_CYCLES / SPLICE_CYCLES / ARITH_MAX / INTERESTING_8|16|32 / MAX_FILE
//   - afl-fuzz.c 的 count_class_lookup8（命中数分桶）与 classify_counts
//   - docs/technical_details.txt 第 2/3/4/5 节（覆盖度量、队列演化、裁剪、trimming）
package main

const (
	// config.h
	mapSizePow2      = 16
	mapSize          = 1 << mapSizePow2 // 65536
	calCycles        = 8
	calCyclesLong    = 40
	tmoutLimit       = 250
	execTimeout      = 1000
	maxFile          = 1 * 1024 * 1024
	havocCycles      = 256
	havocCyclesInit  = 1024
	spliceCycles     = 15
	arithMax         = 35
	tmoutGranularity = 20
)

// CountClass 官方的 8 个桶：1 / 2 / 3 / 4-7 / 8-15 / 16-31 / 32-127 / 128+
func CountClass(count int) int {
	switch {
	case count <= 0:
		return 0
	case count == 1:
		return 1
	case count == 2:
		return 2
	case count == 3:
		return 4
	case count <= 7:
		return 8
	case count <= 15:
		return 16
	case count <= 31:
		return 32
	case count <= 127:
		return 64
	default:
		return 128
	}
}

// ClassifyCounts 对每个字节计数做分桶（官方 classify_counts）
func ClassifyCounts(counters []int) []int {
	out := make([]int, len(counters))
	for i, c := range counters {
		out[i] = CountClass(c)
	}
	return out
}

// Coverage AFL 的共享位图：index = cur ^ prev，prev 存的是 cur >> 1
type Coverage struct {
	MapSize      int
	TraceBits    []int
	PrevLocation int
}

// NewCoverage 构造位图
func NewCoverage(size int) *Coverage {
	return &Coverage{MapSize: size, TraceBits: make([]int, size)}
}

// Reset 清空位图
func (c *Coverage) Reset() {
	for i := range c.TraceBits {
		c.TraceBits[i] = 0
	}
	c.PrevLocation = 0
}

// Visit 进入一个基本块，返回它命中的槽位
func (c *Coverage) Visit(cur int) int {
	index := (cur ^ c.PrevLocation) & (c.MapSize - 1)
	c.TraceBits[index]++
	// 官方：prev = cur >> 1，让 A->B 与 B->A 落到不同槽
	c.PrevLocation = cur >> 1
	return index
}

// Run 跑一串基本块
func (c *Coverage) Run(locations []int) []int {
	c.Reset()
	for _, l := range locations {
		c.Visit(l)
	}
	return c.TraceBits
}

// Checksum trimming 阶段靠它判断「删掉这段没影响执行路径」
func (c *Coverage) Checksum() uint32 {
	h := uint32(0)
	for _, v := range ClassifyCounts(c.TraceBits) {
		h = (h*31 + uint32(v)) & 0xFFFFFFFF
	}
	return h
}

// HasNewBits 0=没新东西 / 1=已知边出现新的命中数桶 / 2=全新的边。
// 官方实现里 virgin_bits[i] == 0xff 表示这条边从没被碰过。
func HasNewBits(trace []int, virgin []int) int {
	ret := 0
	for i, cur := range trace {
		if cur == 0 {
			continue
		}
		if virgin[i]&cur != 0 {
			if ret < 2 {
				if virgin[i] == 0xFF {
					ret = 2
				} else {
					ret = 1
				}
			}
			virgin[i] &= ^cur
		}
	}
	return ret
}

// NewVirgin 全新的 virgin 位图
func NewVirgin(size int) []int {
	v := make([]int, size)
	for i := range v {
		v[i] = 0xFF
	}
	return v
}
