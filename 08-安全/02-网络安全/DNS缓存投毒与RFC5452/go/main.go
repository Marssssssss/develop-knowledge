package main

import "fmt"

const day = 86400.0
const hour = 3600.0

func main() {
	p := DefaultParams()
	r := 7000.0

	fmt.Println("== 1. §7.2 化简常数 ==")
	fmt.Printf("   N*P*I/(D*W) = %.0f（文档：1638400）\n", p.ReducedConstant())
	p1 := p
	p1.NsCount = 1.0
	fmt.Printf("   N=1 时      = %.0f（§8.1 实际用的是它）\n", p1.ReducedConstant())
	p64 := p
	p64.Ports = 64000
	fmt.Printf("   P=64000 时  = %.0f\n", p64.ReducedConstant())

	fmt.Println("\n== 2. §8 TTL=3600 / R=7000 pps ==")
	fmt.Printf("   24 小时 -> P_cs = %.4f\n", p.PCombined(day, 3600.0, r))
	fmt.Printf("   7 天    -> P_cs = %.4f\n", p.PCombined(7*day, 3600.0, r))

	fmt.Println("\n== 3. §8 TTL=60 / R=7000 pps ==")
	fmt.Printf("   24 分钟 -> P_cs = %.4f\n", p.PCombined(1440.0, 60.0, r))
	fmt.Printf("   3 小时  -> P_cs = %.4f\n", p.PCombined(3*hour, 60.0, r))
	fmt.Printf("   9 小时  -> P_cs = %.4f\n", p.PCombined(9*hour, 60.0, r))

	fmt.Println("\n== 4. §7 单次窗口所需带宽 ==")
	fmt.Printf("   65000 包 / 0.1s = %.1f Mbit/s（文档 416）\n", BandwidthBps(65000/0.1)/1e6)
	fmt.Printf("   65000 包 / 1.0s = %.1f Mbit/s（文档 42）\n", BandwidthBps(65000/1.0)/1e6)
	pW1 := p
	pW1.Window = 1.0
	rNeed := pW1.RateForProbability(0.5, hour, 300.0)
	fmt.Printf("   60 分钟 + TTL=300 达 50%%：精确公式需 %.2f Mbit/s（文档说 4）\n",
		BandwidthBps(rNeed)/1e6)

	fmt.Println("\n== 5. §8 的 285 Gb/s 是哪一个口径 ==")
	fmt.Printf("   285 Gb/s / 64000 端口 = %.2f Mbit/s\n", 285e9/64000/1e6)
	fmt.Printf("   416 Mbit/s * 64000    = %.1f Tb/s  <- 不是它\n",
		BandwidthBps(65000/0.1)*64000/1e12)

	fmt.Println("\n== 6. §8.1 有效 TTL=0（窗口 = W = 0.1s）==")
	for _, ns := range []float64{1.0, 2.5} {
		q := p
		q.NsCount = ns
		t := q.PeriodForProbability(0.5, 0, r)
		q64 := q
		q64.Ports = 64000
		t64 := q64.PeriodForProbability(0.5, 0, r)
		fmt.Printf("   N=%.1f  1 端口 -> %.2f s      64000 端口 -> %.1f 小时\n",
			ns, t, t64/hour)
	}

	fmt.Println("\n== 7. 生日攻击（§5）：D 个相同在途查询 ==")
	base := p.PSingleFromRate(r)
	for _, d := range []float64{1, 2, 4, 8, 16} {
		fmt.Printf("   D=%-3.0f P_s = %.6f（%.0f 倍）\n", d, BirthdayPSingle(base, d), d)
	}

	fmt.Println("\n== 8. 问题空间 ==")
	fmt.Printf("   默认 N*P*I = %.0f；端口随机化后 = %.0f（x64000）\n",
		p.ProblemSpace(), p64.ProblemSpace())
}
