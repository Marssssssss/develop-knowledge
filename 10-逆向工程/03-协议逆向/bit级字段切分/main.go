// bit 级字段切分 demo 自检入口：go run . （自检失败即 panic）
package main

import (
	"fmt"
	"math/rand"
)

const (
	corpusN   = 4000
	corpusSeed = 20260919
)

// buildCorpus 语料 = 4000 条 DNS 头标志位，位布局照 RFC 1035 §4.1.1。
func buildCorpus() []int {
	rnd := rand.New(rand.NewSource(corpusSeed))
	out := make([]int, 0, corpusN)
	for k := 0; k < corpusN; k++ {
		qr := rnd.Intn(2)
		opcode := []int{0, 0, 0, 1, 2}[rnd.Intn(5)] // 刻意不含 3 → 非笛卡尔积
		aa := rnd.Intn(2)
		tc := 0
		if rnd.Float64() < 0.1 {
			tc = 1
		}
		rd := 1
		if rnd.Float64() < 0.15 {
			rd = 0
		}
		ra := rnd.Intn(2)
		z := 0 // RFC：Must be zero in all queries and responses
		rcode := []int{0, 1, 2, 3, 5}[rnd.Intn(5)]
		out = append(out, qr<<15|opcode<<11|aa<<10|tc<<9|rd<<8|ra<<7|z<<4|rcode)
	}
	return out
}

func check(label string, cond bool, detail string) {
	if !cond {
		panic("FAIL " + label + " " + detail)
	}
	fmt.Printf("  ok  %-50s %s\n", label, detail)
}

func fmtRuns(rs []run) string {
	s := ""
	for _, r := range rs {
		s += fmt.Sprintf("[%d,%d) ", r.lo, r.hi)
	}
	return s
}

func has(rs []run, lo, hi int) bool {
	for _, r := range rs {
		if r.lo == lo && r.hi == hi {
			return true
		}
	}
	return false
}

func main() {
	msgs := buildCorpus()
	vb := varyingBits(msgs)
	check("变量位共 10 个", len(vb) == 10, fmt.Sprint(vb))

	blocks, cruns := segment(msgs)
	check("bit0（QR）单独成块", has(blocks, 0, 1), fmtRuns(blocks))
	check("bit3-4（Opcode 变量部分）判为同一字段", has(blocks, 3, 5), fmtRuns(blocks))
	check("bit5/6/7/8 各自独立成块",
		has(blocks, 5, 6) && has(blocks, 6, 7) && has(blocks, 7, 8) && has(blocks, 8, 9),
		fmtRuns(blocks))
	check("bit13-15（RCODE 低三位）判为同一字段", has(blocks, 13, 16), fmtRuns(blocks))
	check("常量段 [1,3) 与 [9,13) 无法归属（统计上不可判定）",
		has(cruns, 1, 3) && has(cruns, 9, 13), fmtRuns(cruns))
	check("9 段正好铺满 16 位", len(blocks) == 7 && len(cruns) == 2, fmtRuns(blocks))
	check("常量位开窗基数恒为 1",
		cardinality(msgs, []int{9}) == 1 && cardinality(msgs, []int{12}) == 1, "")
	check("Z 段熵为 0（换信息论判据同样失效）",
		entropyBits(msgs, []int{9, 10, 11}) < 1e-12, "")

	bv := byteView(msgs)
	check("字节粒度只看到 2 个字段", len(bv) == 2, fmt.Sprint(bv))
	check("字节 0 基数 = 2×3×2×2×2 = 48", bv[0] == 48, fmt.Sprint(bv[0]))
	check("字节 1 基数 = 2×5 = 10", bv[1] == 10, fmt.Sprint(bv[1]))
	check("位级 9 段 vs 字节级 2 段", len(blocks)+len(cruns) == 9, "")

	// 反例：语料取满笛卡尔积（opcode 0..15）→ 联合基数 16 = 2^4 → 过度切分
	full := make([]int, 0, 64)
	for qr := 0; qr < 2; qr++ {
		for op := 0; op < 16; op++ {
			for aa := 0; aa < 2; aa++ {
				full = append(full, qr<<15|op<<11|aa<<10)
			}
		}
	}
	fb, _ := segment(full)
	check("笛卡尔积语料下 opcode 被过度切分为 4 个 1 位字段", len(fb) == 6, fmtRuns(fb))
	check("原因：card(bit1..4)=16 恰等于各 bit 基数之积 2^4",
		cardinality(full, []int{1, 2, 3, 4}) == 16, "")
	fmt.Println("bit级字段切分(Go): ALL ASSERTIONS PASSED")
}
