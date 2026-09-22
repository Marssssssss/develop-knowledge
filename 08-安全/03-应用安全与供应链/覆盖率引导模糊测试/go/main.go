// 覆盖率引导模糊测试的演示入口：跑一轮迷你 fuzzing campaign，看覆盖率怎么长出来。
package main

import (
	"fmt"
	"math/rand"
	"sort"
)

// activeTuples 把位图换算成「这条用例踩到了哪些边」
func activeTuples(trace []int) map[int]bool {
	out := map[int]bool{}
	for i, v := range trace {
		if v != 0 {
			out[i] = true
		}
	}
	return out
}

// campaign 一次极简的 fuzz 循环：随机变异 -> 看有没有新覆盖 -> 有就入队
func campaign(seed []byte, rounds int) []*QueueEntry {
	rnd := rand.New(rand.NewSource(20260922))
	cov := NewCoverage(mapSize)
	virgin := NewVirgin(mapSize)
	queue := []*QueueEntry{}
	data := append([]byte{}, seed...)
	deltas := []int{1, -1, 16, 64, 128}
	for i := 0; i < rounds; i++ {
		candidate := append([]byte{}, data...)
		n := 1 + rnd.Intn(4)
		for k := 0; k < n; k++ {
			if len(candidate) == 0 {
				break
			}
			pos := rnd.Intn(len(candidate))
			candidate[pos] = byte((int(candidate[pos]) + deltas[rnd.Intn(len(deltas))]) & 0xFF)
		}
		RunTarget(candidate, cov)
		trace := ClassifyCounts(cov.TraceBits)
		if HasNewBits(trace, virgin) != 0 {
			queue = append(queue, &QueueEntry{
				Name:   fmt.Sprintf("id:%d", len(queue)),
				Data:   append([]byte{}, candidate...),
				Tuples: activeTuples(trace),
				ExecUs: len(candidate),
			})
			data = candidate
		} else if len(candidate) <= len(data) {
			data = candidate
		}
	}
	return queue
}

func main() {
	fmt.Println("覆盖率引导模糊测试：边覆盖位图 + 命中数分桶")
	fmt.Println()
	fmt.Println("1) 命中数分桶（afl-fuzz.c 的 count_class_lookup8）")
	fmt.Println("   桶: 1 / 2 / 3 / 4-7 / 8-15 / 16-31 / 32-127 / 128+")
	for _, n := range []int{1, 2, 3, 4, 7, 8, 15, 16, 31, 32, 127, 128, 255} {
		fmt.Printf("   %4d -> %3d\n", n, CountClass(n))
	}
	fmt.Printf("   31 与 32 跨桶（%d != %d），47 与 48 同桶（%d == %d）\n",
		CountClass(31), CountClass(32), CountClass(47), CountClass(48))

	fmt.Println()
	fmt.Println("2) 一次迷你 fuzzing campaign（种子 \"A\"，4000 轮随机变异）")
	queue := campaign([]byte("A"), 4000)
	all := map[int]bool{}
	for _, q := range queue {
		for t := range q.Tuples {
			all[t] = true
		}
	}
	fmt.Printf("   入队用例数: %d\n", len(queue))
	fmt.Printf("   覆盖的边数: %d\n", len(all))
	if len(queue) > 0 {
		fmt.Printf("   首个新覆盖: %q（%d 字节）\n", queue[0].Data, len(queue[0].Data))
		last := queue[len(queue)-1]
		fmt.Printf("   末个新覆盖: %q（%d 字节）\n", last.Data, len(last.Data))
	}

	fmt.Println()
	fmt.Println("3) 队列裁剪（贪心集合覆盖，score = 延迟 x 大小）")
	entries := []*QueueEntry{
		{Name: "slow-big", Data: bytesRepeat('x', 1000), Tuples: map[int]bool{1: true, 2: true, 3: true}, ExecUs: 1000},
		{Name: "fast-cover", Data: bytesRepeat('y', 10), Tuples: map[int]bool{3: true, 4: true}, ExecUs: 10},
		{Name: "redundant", Data: bytesRepeat('z', 20), Tuples: map[int]bool{1: true, 2: true}, ExecUs: 50},
		{Name: "unique", Data: bytesRepeat('w', 5), Tuples: map[int]bool{9: true}, ExecUs: 20},
	}
	favored := CullQueue(entries)
	names := []string{}
	for _, e := range favored {
		names = append(names, e.Name)
	}
	sort.Strings(names)
	fmt.Printf("   全部 %d 条 -> favored %d 条: %v\n", len(entries), len(favored), names)
	for _, e := range entries {
		fmt.Printf("     %-11s score=%-8d favored=%v\n", e.Name, e.Score(), e.Favored)
	}

	fmt.Println()
	fmt.Println("4) trimming：删掉不影响执行路径的数据")
	padded := append([]byte{0x08, 0x18, 0x28, 0x38}, bytesRepeat('Z', 12)...)
	out := Trim(padded, deadTail)
	fmt.Printf("   %d 字节 -> %d 字节: %q\n", len(padded), len(out), out)
	fmt.Printf("   校验和一致: %v\n", deadTail(out) == deadTail(padded))

	fmt.Println()
	fmt.Printf("5) 常量：MAP_SIZE=%d（2^16），校准每用例跑 %d 遍\n", mapSize, calCycles)
	fmt.Printf("   超时粒度 %d ms，havoc %d 轮，splice %d 轮\n", tmoutGranularity, havocCycles, spliceCycles)
}

// deadTail 目标程序只吃非 'Z' 的字节，所以 'Z' 是可以被 trim 掉的填充
func deadTail(data []byte) uint32 {
	kept := []byte{}
	for _, b := range data {
		if b != 'Z' {
			kept = append(kept, b)
		}
	}
	return RunTarget(kept, nil).Checksum()
}

func bytesRepeat(ch byte, n int) []byte {
	out := make([]byte, n)
	for i := range out {
		out[i] = ch
	}
	return out
}
