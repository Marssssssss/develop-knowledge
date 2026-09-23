package main

import "fmt"

const demoN = 1000

func main() {
	fmt.Println("连续 append", demoN, "次：容量序列前 12 项 / 扩容次数 / 摊销搬移 / 峰值内存")
	fmt.Println("=" + line(67))

	for _, name := range orderedNames {
		seq := appendSequence(name, demoN)
		head := ""
		for i, c := range seq {
			if i >= 12 {
				break
			}
			if i > 0 {
				head += ", "
			}
			head += fmt.Sprint(c)
		}
		moves := amortizedMoves(seq)
		fmt.Printf("\n[%s]\n", name)
		fmt.Printf("  容量前 12 项 : %s\n", head)
		fmt.Printf("  扩容次数     : %d\n", len(growthPoints(seq))-1)
		fmt.Printf("  摊销搬移     : %d 个元素（%.2f×N）\n", moves, float64(moves)/demoN)
		fmt.Printf("  峰值内存比   : %.3f× 最终容量\n", peakMemoryRatio(seq))
	}

	fmt.Println()
	fmt.Println("旧块复用：判据 r^i (2 - r) >= 1")
	fmt.Println("=" + line(67))

	phi := goldenRatio()
	factors := []struct {
		label string
		r     float64
	}{
		{"2", 2.0}, {"1.9", 1.9}, {"1.8", 1.8}, {"φ", phi},
		{"1.5", 1.5}, {"1.25", 1.25}, {"1.1", 1.1},
	}
	for _, f := range factors {
		fmt.Printf("  r = %-6s 实数模型 %-8s | 取整模型 %s\n",
			f.label, genText(reuseGeneration(f.r, 200)), genText(reuseGenerationCeil(f.r, 64)))
	}
	fmt.Printf("\n  φ = %.12f 是「第 2 次扩容就能复用」的上界：r^2(2-r) = 1 的正根。\n", phi)
	fmt.Println("  r = 2 时左边恒为 0 —— 倍增策略在数学上永远无法复用旧块。")
}

func genText(k int) string {
	if k < 0 {
		return "永不复用"
	}
	return fmt.Sprintf("第 %d 次", k)
}

func line(n int) string {
	b := make([]byte, n)
	for i := range b {
		b[i] = '='
	}
	return string(b)
}
