// matching.go — 匹配算法: 排队-配对-对战-更新循环, 与队伍平衡
//
// 与 python/matchmaking.py 的 simulate / team_split 对应。
package main

import (
	"math"
	"sort"
)

// player: 隐藏的真实技能决定胜负, 系统只知道 (mu, sigma) 估计
type player struct {
	pid        int
	arrive     int
	skill      float64
	r          rating
	idleAfter  int
	inQueue    bool
	matched    bool
}

// simResult: 一次模拟的统计量
type simResult struct {
	matches  int
	wait     float64 // 平均等待 tick(每场 2 人参与平均)
	gap      float64 // 平均 |d展示分|
	trueGap  float64 // 平均 |d真技能| —— 真正衡量"公不公平"
	quality  float64 // 平均匹配质量
	tgEarly  float64 // 前半程 |d真技能|(看收敛效应)
	tgLate   float64
	left     int     // 结束时仍滞留队列的人数
	sigma    float64 // 结束时全体平均 sigma
}

// simulate: 每个 tick 放 2 名冷却结束的玩家入队(制造积压), 匹配器尽量成对。
// window = base + rate*wait, 等待越久窗口越宽, 防止高/低分玩家饿死。
// byQuality=true 在窗口内挑匹配质量最大的对手; false 走 FIFO 先到先配。
func simulate(nPlayers, ticks int, base, rate float64, byQuality bool,
	seed uint32, cooldown int) simResult {
	r := newRng(seed)
	ps := make([]*player, nPlayers)
	for i := range ps {
		ps[i] = &player{pid: i, skill: mu0 + 6.0*r.normal(), r: rating{mu0, sigma0}}
	}
	var queue []*player
	var waits, gaps, tgs, quals []float64

	for t := 0; t < ticks; t++ {
		for k := 0; k < 2; k++ { // 到达
			for try := 0; try < 8; try++ {
				i := int(r.u32() % uint32(nPlayers))
				p := ps[i]
				if p.idleAfter <= t && !p.inQueue {
					p.arrive, p.inQueue = t, true
					queue = append(queue, p)
					break
				}
			}
		}
		for _, p := range queue {
			if p.matched {
				continue
			}
			wait := float64(t - p.arrive)
			var best *player
			bestQ := 0.0
			if byQuality {
				window := base + rate*wait
				for _, q := range queue {
					if q == p || q.matched {
						continue
					}
					if math.Abs(display(q.r.mu, q.r.sigma)-display(p.r.mu, p.r.sigma)) > window {
						continue // 出窗口: 宁可继续等
					}
					qq := matchQuality(p.r, q.r)
					if best == nil || qq > bestQ {
						best, bestQ = q, qq
					}
				}
			} else {
				for _, q := range queue {
					if q != p && !q.matched {
						best = q
						break
					}
				}
			}
			if best == nil {
				continue
			}
			p.matched, best.matched = true, true
			waits = append(waits, wait, float64(t-best.arrive))
			gaps = append(gaps, math.Abs(p.r.mu-best.r.mu))
			tg := math.Abs(p.skill - best.skill)
			tgs = append(tgs, tg)
			quals = append(quals, matchQuality(p.r, best.r))

			// 对战: 隐藏技能决定结果
			pa := normCdf((p.skill - best.skill) / math.Sqrt(2.0*beta*beta))
			w, l := p, best
			if r.u() >= pa {
				w, l = best, p
			}
			w.r, l.r = ts1v1(w.r, l.r)
			p.idleAfter, best.idleAfter = t+cooldown, t+cooldown
		}
		keep := queue[:0]
		for _, p := range queue {
			if !p.matched {
				keep = append(keep, p)
			}
			p.matched = false
			p.inQueue = false
		}
		queue = keep
		for _, p := range queue {
			p.inQueue = true
		}
	}

	out := simResult{left: len(queue)}
	m := len(quals)
	if m == 0 {
		return out
	}
	for _, v := range waits {
		out.wait += v
	}
	out.wait /= float64(2 * m)
	for i := range quals {
		out.gap += gaps[i]
		out.trueGap += tgs[i]
		out.quality += quals[i]
	}
	out.gap /= float64(m)
	out.trueGap /= float64(m)
	out.quality /= float64(m)
	half := m / 2
	for i := 0; i < half; i++ {
		out.tgEarly += tgs[i]
	}
	for i := half; i < m; i++ {
		out.tgLate += tgs[i]
	}
	if half > 0 {
		out.tgEarly /= float64(half)
	}
	if m-half > 0 {
		out.tgLate /= float64(m - half)
	}
	for _, p := range ps {
		out.sigma += p.r.sigma
	}
	out.sigma /= float64(nPlayers)
	out.matches = m
	return out
}

// =====================================================================
// 队伍平衡: 把 2n 人分成两队, 使两队总分差最小(划分问题, NP-hard)
// =====================================================================

// greedySplit: 从高到低依次丢给"当前总分较低"的队
func greedySplit(ratings []float64) ([]float64, []float64) {
	s := append([]float64(nil), ratings...)
	sort.Sort(sort.Reverse(sort.Float64Slice(s)))
	a, b := []float64{}, []float64{}
	for _, v := range s {
		if sum(a) <= sum(b) {
			a = append(a, v)
		} else {
			b = append(b, v)
		}
	}
	return a, b
}

// localSearchSplit: 反复交换两队各一名队员, 直到无法改进
func localSearchSplit(a, b []float64) ([]float64, []float64) {
	a, b = append([]float64(nil), a...), append([]float64(nil), b...)
	for improved := true; improved; {
		improved = false
		for i := range a {
			for j := range b {
				cur := math.Abs(sum(a) - sum(b))
				a[i], b[j] = b[j], a[i]
				if math.Abs(sum(a)-sum(b)) < cur {
					improved = true
				} else {
					a[i], b[j] = b[j], a[i]
				}
			}
		}
	}
	return a, b
}

// exactSplit: 2^n 枚举(仅小规模参照)
func exactSplit(ratings []float64) float64 {
	n := len(ratings)
	best := -1.0
	for mask := 0; mask < 1<<n; mask++ {
		if popcount(mask) != n/2 {
			continue
		}
		sa, sb := 0.0, 0.0
		for i := 0; i < n; i++ {
			if mask>>i&1 == 1 {
				sa += ratings[i]
			} else {
				sb += ratings[i]
			}
		}
		if d := math.Abs(sa - sb); best < 0 || d < best {
			best = d
		}
	}
	if best < 0 {
		return 0
	}
	return best
}

// teamSplitDiffs: 返回 (贪心差, 局部搜索差, 精确最优差)
func teamSplitDiffs(ratings []float64) (float64, float64, float64) {
	ga, gb := greedySplit(ratings)
	la, lb := localSearchSplit(ga, gb)
	return math.Abs(sum(ga) - sum(gb)), math.Abs(sum(la) - sum(lb)),
		exactSplit(ratings)
}

func sum(v []float64) float64 {
	s := 0.0
	for _, x := range v {
		s += x
	}
	return s
}

func popcount(x int) int {
	c := 0
	for ; x != 0; x >>= 1 {
		c += x & 1
	}
	return c
}
