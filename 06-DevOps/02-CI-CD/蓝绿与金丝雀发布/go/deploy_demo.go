// deploy_demo.go — 蓝绿与金丝雀发布(四种部署策略模拟 + 分析门控自动回滚)
//
// 统一 LCG 随机数(state = 1664525*state + 1013904223 mod 2^32),
// 三种语言输出可逐行比对:
//
//	demo 1 Recreate vs RollingUpdate 停机对比
//	demo 2 Blue-Green 切流 + 瞬时回滚 / 晋升
//	demo 3 Canary 步进分权重 + 分析门控(坏版本中止 / 好版本晋升)
package main

import "fmt"

const (
	lcgA = 1664525
	lcgC = 1013904223
)

// LCG 确定性伪随机数:与 C/Python 版逐位一致,便于跨语言比对输出。
type lcg uint32

func (g *lcg) rnd() float64 {
	*g = lcg(uint32(*g)*uint32(lcgA) + uint32(lcgC))
	return float64(*g) / 4294967296.0
}

// simulateRecreate Recreate:先删旧再启新;切换窗口内的请求全部失败(必然停机)。
func simulateRecreate(n int, errNew float64, seed uint32, gap float64) (errors, gapN int) {
	g := lcg(seed)
	gapN = int(float64(n) * gap)
	for i := 0; i < n; i++ {
		if i < gapN {
			errors++ // 停机窗口:没有版本在服务
		} else if g.rnd() < errNew {
			errors++ // 新版本自身错误
		}
	}
	return
}

// simulateRolling RollingUpdate:逐实例替换;每轮 r/replicas 的请求到新版本,无停机。
func simulateRolling(n int, errNew float64, seed uint32, replicas int) int {
	g := lcg(seed)
	errors, batch := 0, n/replicas
	for r := 1; r <= replicas; r++ {
		for i := 0; i < batch; i++ {
			if g.rnd() < float64(r)/float64(replicas)*errNew {
				errors++
			}
		}
		fmt.Printf("    round %d: %d/%d new pods\n", r, r, replicas)
	}
	return errors
}

// simulateBlueGreen Blue-Green:切流瞬时;analysisAfter 个请求后统计错误率决定回切/晋升。
// 回滚瞬时且零额外错误的根因:旧 ReplicaSet 未缩容,切回只是改 selector。
func simulateBlueGreen(n int, errNew float64, seed uint32, threshold float64,
	analysisAfter int) (errors int, rate float64, verdict string) {
	g := lcg(seed)
	for i := 0; i < analysisAfter; i++ { // 切流后新版本全量服务
		if g.rnd() < errNew {
			errors++
		}
	}
	rate = float64(errors) / float64(analysisAfter)
	if rate > threshold {
		return errors, rate, "rolled back (switch activeService to old RS)"
	}
	for i := 0; i < n-analysisAfter; i++ { // 晋升:剩余全走新版本
		if g.rnd() < errNew {
			errors++
		}
	}
	return errors, rate, "promoted (old RS scaled down)"
}

// simulateCanary Canary:按步分权重;每步统计新版本错误率,超阈值即中止回滚。
func simulateCanary(n int, errNew float64, seed uint32, steps []int,
	threshold float64) (int, string) {
	g := lcg(seed)
	errors, batch := 0, n/len(steps)
	for _, w := range steps {
		newTotal, newErr := 0, 0
		for i := 0; i < batch; i++ {
			if g.rnd() < float64(w)/100.0 { // 路由:weight% 到新版本
				newTotal++
				if g.rnd() < errNew { // 新版本自身错误率
					newErr++
					errors++
				}
			} // 旧版本(稳定)错误率视为 0
		}
		if newTotal > 0 && float64(newErr)/float64(newTotal) > threshold {
			return errors, fmt.Sprintf("aborted at %d%% (err=%d/%d > %.2f)",
				w, newErr, newTotal, threshold)
		}
	}
	return errors, "promoted to 100%"
}

func main() {
	const n = 1000
	steps := []int{10, 33, 100}

	fmt.Println("== demo 1: Recreate vs RollingUpdate (n=1000, 10% 切换窗口) ==")
	errors, gapN := simulateRecreate(n, 0.0, 42, 0.10)
	fmt.Printf("  Recreate: %d errors (%d 请求落在停机窗口)"+
		" — 两版本从不共存,但必然停机\n", errors, gapN)
	fmt.Println("  Rolling:  0 errors — 无停机,但版本共存:")
	simulateRolling(n, 0.0, 42, 5)

	fmt.Println("\n== demo 2: Blue-Green (n=1000, analysis after 200 req) ==")
	errors, rate, verdict := simulateBlueGreen(n, 0.20, 7, 0.05, 200)
	fmt.Printf("  坏版本(err=20%%): %d errors, 检测错误率 %.2f -> %s\n",
		errors, rate, verdict)
	fmt.Println("    回滚零额外成本:旧 RS 未缩容,切回 selector 即恢复")
	errors, rate, verdict = simulateBlueGreen(n, 0.0, 7, 0.05, 200)
	fmt.Printf("  好版本(err=0%%):  %d errors, 检测错误率 %.2f -> %s\n",
		errors, rate, verdict)
	fmt.Println("    切换期间 2x 副本成本是蓝绿的代价")

	fmt.Println("\n== demo 3: Canary steps=[10,33,100] (n=1000, threshold=5%) ==")
	errors, verdict = simulateCanary(n, 0.20, 9, steps, 0.05)
	fmt.Printf("  坏版本(err=20%%): %d errors -> %s\n", errors, verdict)
	fmt.Println("    爆炸半径被限制在第 1 步的 10% 流量内")
	errors, verdict = simulateCanary(n, 0.0, 9, steps, 0.05)
	fmt.Printf("  好版本(err=0%%):  %d errors -> %s\n", errors, verdict)
	fmt.Println("    三步分析全过,新版本晋升为 stable")
}
