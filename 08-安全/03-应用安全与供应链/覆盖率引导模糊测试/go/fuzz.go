// 覆盖率引导模糊测试（AFL 模型）：队列演化、贪心裁剪、trimming 与变异策略。
//
// 对应 afl-fuzz.c 的 cull_queue / trim_case 与 config.h 的确定性变异参数。
package main

import "sort"

// QueueEntry 队列里的一条用例
type QueueEntry struct {
	Name    string
	Data    []byte
	Tuples  map[int]bool
	ExecUs  int
	Favored bool
}

// Score 官方：score ∝ 执行延迟 × 文件大小，越小越划算
func (q *QueueEntry) Score() int {
	size := len(q.Data)
	if size < 1 {
		size = 1
	}
	return q.ExecUs * size
}

func containsEntry(list []*QueueEntry, e *QueueEntry) bool {
	for _, x := range list {
		if x == e {
			return true
		}
	}
	return false
}

// CullQueue 贪心集合覆盖：为每条边挑分数最低的候选，顺序遍历未覆盖的边
func CullQueue(entries []*QueueEntry) []*QueueEntry {
	bestFor := map[int]*QueueEntry{}
	for _, e := range entries {
		for t := range e.Tuples {
			cur, ok := bestFor[t]
			if !ok || e.Score() < cur.Score() {
				bestFor[t] = e
			}
		}
	}
	covered := map[int]bool{}
	favored := []*QueueEntry{}
	for _, e := range entries {
		allCovered := true
		seed := -1
		for t := range e.Tuples {
			if !covered[t] {
				allCovered = false
				seed = t
				break
			}
		}
		if allCovered {
			// 已经被选过的条目要保住 favored 标记，不能因为「现在被覆盖了」就清掉
			if !containsEntry(favored, e) {
				e.Favored = false
			}
			continue
		}
		winner := bestFor[seed]
		if containsEntry(favored, winner) {
			for t := range e.Tuples {
				covered[t] = true
			}
			e.Favored = false
			continue
		}
		winner.Favored = true
		favored = append(favored, winner)
		for t := range winner.Tuples {
			covered[t] = true
		}
	}
	return favored
}

// Trim afl-fuzz 内置 trimmer：按块删，只要 trace 校验和不变就落盘
func Trim(data []byte, runChecksum func([]byte) uint32) []byte {
	baseline := runChecksum(data)
	current := append([]byte{}, data...)
	for _, size := range []int{16, 8, 4, 2, 1} {
		i := 0
		for i < len(current) {
			candidate := append([]byte{}, current[:i]...)
			if i+size < len(current) {
				candidate = append(candidate, current[i+size:]...)
			}
			if len(candidate) == 0 {
				break
			}
			if runChecksum(candidate) == baseline {
				current = candidate
			} else {
				i += size
			}
		}
	}
	return current
}

// InterestingValues config.h 的 INTERESTING_8 / 16 / 32
func InterestingValues() map[int][]int {
	return map[int][]int{
		8:  {-128, -1, 0, 1, 16, 32, 64, 100, 127},
		16: {-32768, -129, 128, 255, 256, 512, 1000, 1024, 4096, 32767},
		32: {-2147483648, -100663046, -32769, 32768, 65535, 65536, 100663045, 2147483647},
	}
}

// BitflipStages 确定性 bitflip 的步长序列
func BitflipStages() [][2]int {
	return [][2]int{{1, 1}, {2, 1}, {4, 1}, {8, 8}, {16, 8}, {32, 8}}
}

// ArithOffsets 确定性 arith：加减 [1, ARITH_MAX]
func ArithOffsets() []int {
	out := make([]int, 0, arithMax)
	for i := 1; i <= arithMax; i++ {
		out = append(out, i)
	}
	return out
}

// TimeoutMs 文档：5x 初始校准速度，向上取整到 20 ms 的倍数
func TimeoutMs(execMs int) int {
	raw := execMs * 5
	if raw <= 0 {
		return tmoutGranularity
	}
	rounded := ((raw + tmoutGranularity - 1) / tmoutGranularity) * tmoutGranularity
	if rounded < tmoutGranularity {
		return tmoutGranularity
	}
	return rounded
}

// SkipProbability 非 favored 条目被跳过的概率（文档 4 节）
func SkipProbability(hasNewFavorites, fuzzedBefore bool) float64 {
	if hasNewFavorites {
		return 0.99
	}
	if fuzzedBefore {
		return 0.95
	}
	return 0.75
}

// TargetLocations 玩具目标：每个字节 -> 一个块号，低 3 位决定进入几次
func TargetLocations(data []byte) []int {
	locs := []int{}
	for _, b := range data {
		block := int(b>>3) & 0x3F
		times := int(b&0x07) + 1
		for i := 0; i < times; i++ {
			locs = append(locs, block)
		}
	}
	return locs
}

// RunTarget 用玩具目标跑一次，返回位图
func RunTarget(data []byte, cov *Coverage) *Coverage {
	if cov == nil {
		cov = NewCoverage(mapSize)
	}
	cov.Run(TargetLocations(data))
	return cov
}

// SortNames 演示用的稳定排序（与区域设置无关）
func SortNames(names []string) []string {
	out := append([]string{}, names...)
	sort.Strings(out)
	return out
}
