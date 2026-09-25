// leaderboard.go — 与 python/leaderboard.py 同语义的 Go 复刻(静态审查用)。
package main

import (
	"fmt"
	"sort"
)

type entry struct {
	member string
	score  float64
}

// ZSet:score 为主键,同分按成员字节序(官方语义)。
type ZSet struct{ scores map[string]float64 }

func NewZSet() *ZSet { return &ZSet{scores: map[string]float64{}} }

func (z *ZSet) ZAdd(m string, s float64)      { z.scores[m] = s }
func (z *ZSet) ZIncrBy(m string, d float64) float64 {
	z.scores[m] += d
	return z.scores[m]
}

func (z *ZSet) sorted() []entry {
	out := make([]entry, 0, len(z.scores))
	for m, s := range z.scores {
		out = append(out, entry{m, s})
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].score != out[j].score {
			return out[i].score < out[j].score
		}
		return out[i].member < out[j].member // 字节序
	})
	return out
}

func (z *ZSet) ZRank(m string) int {
	for i, e := range z.sorted() {
		if e.member == m {
			return i
		}
	}
	return -1
}

// CompositeScore:高位分数、低位 (tsMax-ts),同分时更早达成者更高。
func CompositeScore(score float64, ts, tsMax int) float64 {
	return score*float64(tsMax) + float64(tsMax-1-ts)
}

func main() {
	z := NewZSet()
	z.ZAdd("alice", 10)
	z.ZAdd("bob", 30)
	z.ZAdd("carol", 20)
	fmt.Println("rank alice:", z.ZRank("alice")) // 0

	t := map[string]int{"zoe": 100, "mia": 300, "adam": 500}
	z2 := NewZSet()
	for _, m := range []string{"zoe", "mia", "adam"} {
		z2.ZAdd(m, CompositeScore(100, t[m], 1<<20))
	}
	items := z2.sorted()
	fmt.Println("earliest first:", items[0].member) // zoe
}
