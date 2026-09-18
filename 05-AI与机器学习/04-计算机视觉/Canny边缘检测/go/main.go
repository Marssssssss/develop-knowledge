// Canny 边缘检测的 Go 自检入口(与 python/canny_check.py 同题)。
//
// 断言只挑「结构性成立」的结论:核的总和与近似度、四个方向的量化边界、
// 阶跃必须收成 1 像素宽、双阈值三档行为。浮点细节与坑的量化留给 Python 版。
package main

import (
	"fmt"
	"math"
	"os"
)

func main() {
	fmt.Println("== A. 高斯核 ==")
	sum := 0
	for _, row := range gaussian5x5Int {
		for _, v := range row {
			sum += v
		}
	}
	check("1/159 整数核总和 == 159", sum == gaussianDivisor, fmt.Sprintf("sum=%d", sum))
	k := gaussianKernel(5, 1.4)
	ksum := 0.0
	for _, row := range k {
		for _, v := range row {
			ksum += v
		}
	}
	check("连续高斯核归一化", math.Abs(ksum-1.0) < 1e-14, fmt.Sprintf("sum=%.16f", ksum))
	worst := 0.0
	for i := 0; i < 5; i++ {
		for j := 0; j < 5; j++ {
			ref := float64(gaussian5x5Int[i][j]) / gaussianDivisor
			d := math.Abs(k[i][j]-ref) / ref
			if d > worst {
				worst = d
			}
		}
	}
	check("整数核是 sigma≈1.4 高斯的近似", worst < 0.09, fmt.Sprintf("最大相对偏差 %.4f", worst))

	fmt.Println("\n== B. Sobel 梯度 ==")
	step := stepImage(9, 9, 3)
	gx, gy := sobel(step)
	gyMax := 0.0
	for _, row := range gy {
		for _, v := range row {
			if math.Abs(v) > gyMax {
				gyMax = math.Abs(v)
			}
		}
	}
	check("竖直阶跃上 Gy 恒为 0", gyMax == 0, fmt.Sprintf("max|Gy|=%.6f", gyMax))
	check("阶跃两侧 |Gx| 相等(=4*200)", math.Abs(gx[4][2]) == 800 && math.Abs(gx[4][3]) == 800,
		fmt.Sprintf("|Gx|=%.1f/%.1f", math.Abs(gx[4][2]), math.Abs(gx[4][3])))
	m1 := magnitude(gx, gy, false)
	m2 := magnitude(gx, gy, true)
	ok := true
	for y := range m1 {
		for x := range m1[y] {
			if m2[y][x] > m1[y][x]+1e-9 {
				ok = false
			}
		}
	}
	check("L2 幅值恒 <= L1 幅值", ok, "sqrt 不等式")

	fmt.Println("\n== C. 方向量化 ==")
	for _, tc := range []struct {
		deg  float64
		want int
	}{{0, 0}, {22.4, 0}, {22.5, 1}, {67.4, 1}, {67.5, 2}, {112.4, 2}, {112.5, 3}, {157.4, 3}, {157.5, 0}} {
		check(fmt.Sprintf("%.1f 度 -> 档 %d", tc.deg, tc.want), sector(tc.deg) == tc.want,
			fmt.Sprintf("got %d", sector(tc.deg)))
	}
	check("(1,1) 方向为 45 度", sector(angle180(1, 1)) == 1, "")
	check("(-1,1) 方向为 135 度", sector(angle180(-1, 1)) == 3, "")

	fmt.Println("\n== D. 非极大值抑制 ==")
	b := blur(step)
	bgx, bgy := sobel(b)
	bmag := magnitude(bgx, bgy, false)
	bsec := make([][]int, 9)
	for y := 0; y < 9; y++ {
		bsec[y] = make([]int, 9)
		for x := 0; x < 9; x++ {
			bsec[y][x] = sector(angle180(bgx[y][x], bgy[y][x]))
		}
	}
	thin := nonMax(bmag, bsec, 1e-9)
	perRow := make([]int, 9)
	for y := 0; y < 9; y++ {
		for x := 0; x < 9; x++ {
			if thin[y][x] > 0 {
				perRow[y]++
			}
		}
	}
	allOne := true
	for _, v := range perRow {
		if v != 1 {
			allOne = false
		}
	}
	check("对称阶跃每行恰 1 个边缘像素", allOne, fmt.Sprintf("%v", perRow))
	check("两候选峰数学平局", math.Abs(bmag[4][2]-bmag[4][3]) < 1e-9,
		fmt.Sprintf("%.4f vs %.4f", bmag[4][2], bmag[4][3]))

	fmt.Println("\n== E. 双阈值 + 滞后 ==")
	grid := make([][]float64, 7)
	for y := 0; y < 7; y++ {
		grid[y] = make([]float64, 7)
	}
	grid[1][1], grid[1][2], grid[1][3], grid[1][4] = 100, 40, 40, 40
	grid[6][6] = 40
	grid[5][1] = 30
	edges := hysteresis(grid, 20, 60)
	keep := 0
	for _, row := range edges {
		for _, v := range row {
			keep += v
		}
	}
	check("强边 + 连通弱边被保留(4 个)", keep == 4 &&
		edges[1][1] == 1 && edges[1][4] == 1, fmt.Sprintf("keep=%d", keep))
	check("孤立弱边被丢弃(OpenCV 教程的 edge B)", edges[6][6] == 0, "")

	fmt.Println("\n== F. 整条流水线 ==")
	e := canny(step, 100, 300)
	check("阶跃图边缘恰 1 列 x 9 行", countEdges(e) == 9, fmt.Sprintf("%d", countEdges(e)))
	e2 := canny(step, 100, 500)
	check("high 抬到峰之上后无边缘", countEdges(e2) == 0, fmt.Sprintf("%d", countEdges(e2)))
	flat := make([][]float64, 8)
	for y := 0; y < 8; y++ {
		flat[y] = make([]float64, 8)
		for x := 0; x < 8; x++ {
			flat[y][x] = 128
		}
	}
	check("常量图无边缘", countEdges(canny(flat, 10, 30)) == 0, "")
	check("坑:阈值取 0 时常量图整幅成边", countEdges(canny(flat, 0, 0)) == 64,
		fmt.Sprintf("%d/64", countEdges(canny(flat, 0, 0))))

	fmt.Printf("\n结果:%d/%d 通过", passed, total)
	if len(fails) > 0 {
		fmt.Printf(",失败:%v", fails)
		os.Exit(1)
	}
	fmt.Println()
}
