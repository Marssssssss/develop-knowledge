package main

import "fmt"

func main() {
	fmt.Println("autovacuum 阈值与事务 ID 回绕(Go 转写版)")

	fmt.Println("\n== 1. 阈值随表增长, 1 亿封顶 ==")
	for _, n := range []float64{0, 100, 10000, 1e6, 1e8, 1e9} {
		vt, _, at := Thresholds(n, 0, 0)
		fmt.Printf("  %-12.0f vacthresh=%-12.0f anlthresh=%.0f\n", n, vt, at)
	}

	fmt.Println("\n== 2. 同一份死元组 ==")
	for _, n := range []float64{100, 10000, 1e6} {
		d := NeedsVacanalyze(relOpts{Reltuples: n, DeadTuples: 2000, AVEnabled: true,
			Relfrozenxid: -1, RecentXid: -1})
		fmt.Printf("  %-9.0f 行 阈值=%-10.0f 死元组 2000 -> vacuum=%-5v score=%.2f\n",
			n, d.Thresh[0], d.DoVacuum, d.Scores.Vac)
	}

	fmt.Println("\n== 3. 纯插入表(未冻结比例) ==")
	for _, c := range [][2]int{{0, 0}, {1000, 0}, {1000, 500}, {1000, 1000}} {
		_, vit, _ := Thresholds(100000, c[0], c[1])
		fmt.Printf("  pages=%-5d allfrozen=%-5d 未冻结=%.2f insert 阈值=%.0f\n",
			c[0], c[1], UnfrozenRatio(c[0], c[1]), vit)
	}

	fmt.Println("\n== 4. XID 四条防线(oldest=1000) ==")
	l := XidLimits(1000)
	fmt.Printf("  vac  = %-12d 剩余 %.4f%%\n", l.Vac, RemainingPct(l.Vac, l.Wrap))
	fmt.Printf("  warn = %-12d 剩余 %.4f%%\n", l.Warn, RemainingPct(l.Warn, l.Wrap))
	fmt.Printf("  stop = %-12d 剩余 %.4f%%\n", l.Stop, RemainingPct(l.Stop, l.Wrap))
	fmt.Printf("  wrap = %-12d 剩余 %.4f%%\n", l.Wrap, RemainingPct(l.Wrap, l.Wrap))
	fmt.Println("  判定:", Classify(l.Vac, l), Classify(l.Warn, l), Classify(l.Stop, l), Classify(l.Wrap, l))

	fmt.Println("\n== 5. 防回绕强制 vacuum(autovacuum 关闭也做) ==")
	f := NeedsVacanalyze(relOpts{Reltuples: 0, RecentXid: 300000000, Relfrozenxid: 0, AVEnabled: false})
	fmt.Printf("  av_enabled=false, relfrozenxid 落后 3 亿 -> dovacuum=%v wraparound=%v\n",
		f.DoVacuum, f.Wraparound)
	n := NeedsVacanalyze(relOpts{Reltuples: 0, DeadTuples: 50, AVEnabled: false,
		Relfrozenxid: -1, RecentXid: -1})
	fmt.Printf("  av_enabled=false, 普通死元组 50 -> dovacuum=%v(负控)\n", n.DoVacuum)

	fmt.Println("\n  静态表强制 vacuum 间隔 =", FreezeInterval(), "个事务")
}
