// selftest.go — 五段自检(与 python/matchmaking.py、c/matchmaking.c 输出一致)
package main

import (
	"fmt"
	"math"
	"os"
)

var fails int

func check(cond bool, what string) {
	if !cond {
		fmt.Printf("  [FAIL] %s\n", what)
		fails++
	}
}

func secElo() {
	fmt.Println("== 一、Elo: 400 分标尺 + K 因子 ==")
	eEq, e400, eNeg := eloExpect(1500, 1500), eloExpect(1900, 1500), eloExpect(1100, 1500)
	check(math.Abs(eEq-0.5) < 1e-12, "同分期望应为 0.5")
	check(math.Abs(e400-0.909) < 0.001 && math.Abs(eNeg-0.091) < 0.001, "400 分差应约 91%/9%")
	fmt.Printf("  同分 -> 期望 %.3f; 高 400 分 -> %.3f; 低 400 分 -> %.3f\n", eEq, e400, eNeg)

	w, l := eloUpdate(1500, 1500, 1.0)
	fmt.Printf("  K=%.0f, 1500 vs 1500 胜: 1500 -> %.0f; 负方 -> %.0f (-16)\n", eloK, w, l)
	check(w == 1516.0 && l == 1484.0, "同分对局应各变 16 分")

	w2, l2 := eloUpdate(1500, 1900, 1.0)
	fmt.Printf("  弱胜强(1500 赢 1900): +%.2f / %.2f —— 期望仅 %.3f, 故单局收益很大\n",
		w2-1500, l2-1900, eloExpect(1500, 1900))
	check(w2-1500 > 25.0 && l2-1900 < -25.0, "爆冷的单局变化应远大于 16")
	fmt.Println("  -> 400 分差只是'约 91% 胜率', 不是必胜; 强方赢球几乎不加分, 输球重罚  OK")
}

func secTrueSkill() {
	fmt.Println("\n== 二、TrueSkill: (mu, sigma) 与保守估计 ==")
	fmt.Printf("  新玩家 mu=%.0f, sigma=%.3f -> 展示分 %.1f; 区间 [mu-3sigma, mu+3sigma] = [%.0f, %.0f]\n",
		mu0, sigma0, display(mu0, sigma0), display(mu0, sigma0), mu0+3*sigma0)
	check(math.Abs(display(mu0, sigma0)) < 1e-9, "新玩家展示分应为 0")

	w, l := ts1v1(rating{mu0, sigma0}, rating{mu0, sigma0})
	fmt.Printf("  新手 vs 新手(一方赢): 胜者 mu %.2f->%.2f, sigma %.2f->%.2f, 展示分 0.0 -> %.2f\n",
		mu0, w.mu, sigma0, w.sigma, display(w.mu, w.sigma))
	fmt.Printf("                        负者 mu %.2f->%.2f, sigma %.2f->%.2f, 展示分 0.0 -> %.2f\n",
		mu0, l.mu, sigma0, l.sigma, display(l.mu, l.sigma))
	check(w.mu > mu0 && w.sigma < sigma0 && l.mu < mu0 && l.sigma < sigma0, "胜者升负者降且 sigma 均降")
	sum := display(w.mu, w.sigma) + display(l.mu, l.sigma)
	fmt.Printf("  两者展示分之和 = %.2f != 0 —— TrueSkill **不是零和**(不确定性下降本身就是收益)\n", sum)
	check(math.Abs(sum) > 1e-9, "展示分之和不应为 0")

	nw, nl := ts1v1(rating{mu0, sigma0}, rating{30.0, 2.0})
	fmt.Printf("  新手爆冷击败老手(mu=30, sigma=2.0): 新手 mu %.2f->%.2f (+%.2f), sigma %.2f->%.2f\n",
		mu0, nw.mu, nw.mu-mu0, sigma0, nw.sigma)
	fmt.Printf("                                   老手 mu 30.00->%.2f (%+.2f), sigma 2.00->%.2f\n",
		nl.mu, nl.mu-30.0, nl.sigma)
	check(nw.mu-mu0 > 30.0-nl.mu, "sigma 大的一方变化应更大")
	fmt.Printf("  -> 更新权重 ~ sigma^2/(2beta^2+sigma_w^2+sigma_l^2): 同样的胜负, 新手动 %.2f 分, 老手只动 %.2f 分  OK\n",
		nw.mu-mu0, 30.0-nl.mu)

	cur, opp := rating{mu0, sigma0}, rating{mu0, sigma0}
	for i := 0; i < 12; i++ {
		cur, _ = ts1v1(cur, opp)
	}
	fmt.Printf("  连续胜 12 局(对手始终是新手): mu %.2f->%.2f, sigma %.3f->%.3f (展示分 %.2f)\n",
		mu0, cur.mu, sigma0, cur.sigma, display(cur.mu, cur.sigma))
	check(cur.sigma < sigma0*0.75, "12 局后 sigma 应明显收缩")
	fmt.Printf("  注: sigma 每次赛前 +tau=%.4f 的'动量', 因此**永不归零**(技能会随时间变化)  OK\n", tau)
	fmt.Println("  对照 MSR 页面给出的收敛场次: 2 人 12 局、2v2*2 队 10 局、4v4 46 局 —— 人越多/队越大, 单次结果信息量越少")
}

func secQuality() {
	fmt.Println("\n== 三、匹配质量: 展示分差 != 匹配好坏 ==")
	newbie, vet, bad := rating{mu0, sigma0}, rating{31.0, 2.0}, rating{2.5, 0.5}
	cases := []struct {
		label string
		a, b  rating
	}{
		{"新手(展示 0) vs 老手(展示 25, sigma=2)", newbie, vet},
		{"新手(展示 0) vs 差手(展示 1, sigma=0.5)", newbie, bad},
		{"两个新手(展示 0) vs (0)", newbie, newbie},
		{"两个老手(展示 25) vs (25), sigma=2", vet, vet},
	}
	q := make([]float64, len(cases))
	for i, c := range cases {
		q[i] = matchQuality(c.a, c.b)
		gap := math.Abs(display(c.a.mu, c.a.sigma) - display(c.b.mu, c.b.sigma))
		fmt.Printf("  %-40s 展示分差 %5.1f -> 质量 %.3f\n", c.label, gap, q[i])
	}
	check(q[0] > q[1], "展示分差大的匹配质量反而应更高(不确定性主导)")
	fmt.Printf("  -> 反直觉但正确: 展示分差大(25 级)的一方质量 %.3f, 反而高于展示分差仅 1 级的 %.3f\n", q[0], q[1])
	fmt.Println("     原因: 差手已高度确定(sigma=0.5, 真实 mu 只有 2.5), 新手对他毫无可学之物  OK")
	check(q[3] > q[2], "mu 相同时 sigma 越小质量越高")
	fmt.Printf("  -> mu 完全相同(mu=25)时: 两个新手质量仅 %.3f, 两个老手 %.3f; sigma 大 => 质量明显小于 1  OK\n", q[2], q[3])
	fmt.Println("  -> MSR 页面例子同此规律: 新手 vs 老手 展示分差 23 级仍有 57.6% 质量,")
	fmt.Println("     而新手 vs '摆烂老手' 展示分差仅 1 级、质量只有 5.7% (不确定性主导) ")
}

func secMatch() {
	fmt.Println("\n== 四、匹配算法: 窗口扩张(等待 vs 公平) ==")
	type strat struct {
		label            string
		byQuality        bool
		base, rate       float64
	}
	cases := []strat{
		{"FIFO(先到先配)", false, 0.0, 0.0},
		{"窗口 8+0.5*t 最大质量", true, 8.0, 0.5},
		{"窗口 3+0.2*t 最大质量", true, 3.0, 0.2},
	}
	fmt.Printf("  %-24s%6s%10s%11s%11s%10s%6s%9s\n", "策略", "对局", "平均等待",
		"|d展示分|", "|d真技能|", "平均质量", "余留", "均sigma")
	rs := make([]simResult, len(cases))
	for i, c := range cases {
		rs[i] = simulate(120, 1500, c.base, c.rate, c.byQuality, 12345, 5)
		fmt.Printf("  %-24s%6d%10.2f%11.2f%11.2f%10.3f%6d%9.3f\n", c.label, rs[i].matches,
			rs[i].wait, rs[i].gap, rs[i].trueGap, rs[i].quality, rs[i].left, rs[i].sigma)
	}
	fifo, w8, w3 := rs[0], rs[1], rs[2]
	check(w3.trueGap < w8.trueGap && w8.trueGap < fifo.trueGap, "窗口越紧真实技能差应越小")
	check(w3.wait > fifo.wait && w3.quality > fifo.quality, "越公平代价是等待更长")
	fmt.Printf("  -> 窗口越紧: 真实技能差 %.2f -> %.2f -> %.2f(越公平), 代价是等待 %.2f -> %.2f tick\n",
		fifo.trueGap, w8.trueGap, w3.trueGap, fifo.wait, w3.wait)
	fmt.Printf("  -> sigma 从 %.2f 收敛到 %.2f(120 名玩家 / 1500 tick), 估计越准匹配越准:\n", sigma0, w3.sigma)
	fmt.Printf("     窗口 3 策略前半程 |d真技能| %.2f vs 后半程 %.2f  OK\n", w3.tgEarly, w3.tgLate)
	check(w3.tgLate < w3.tgEarly, "收敛后应比冷启动更准")
	fmt.Println("  注: FIFO 的 |d展示分| 与 |d真技能| 都最大 —— 先到先配等价于随机配, 排名系统再好也白搭")
}

func secTeam() {
	fmt.Println("\n== 五、队伍平衡: 贪心 vs 局部搜索 vs 精确最优 ==")
	r := newRng(999)
	for n := 10; n <= 14; n += 2 {
		ratings := make([]float64, n)
		for i := range ratings {
			ratings[i] = math.Round((1000.0+400.0*r.normal())*10.0) / 10.0
		}
		g, ls, ex := teamSplitDiffs(ratings)
		fmt.Printf("  %d 人分两队(评分 1000+-400): 贪心差 %7.1f | 局部搜索差 %7.1f | 精确最优 %7.1f\n",
			n, g, ls, ex)
		check(ex <= ls+1e-6, "精确最优不应差于局部搜索")
		check(ls <= g+1e-9, "局部搜索不应差于贪心")
	}
	fmt.Println("  -> 贪心(排序后交替分发)偏差最大; 一个交换式局部搜索即可逼近 2^n 枚举的最优  OK")
}

func main() {
	secElo()
	secTrueSkill()
	secQuality()
	secMatch()
	secTeam()
	if fails == 0 {
		fmt.Println("\n全部自检通过。")
		return
	}
	fmt.Printf("\n存在 %d 项失败。\n", fails)
	os.Exit(1)
}
