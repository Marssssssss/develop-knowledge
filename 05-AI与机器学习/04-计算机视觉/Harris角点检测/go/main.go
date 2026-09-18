// Harris 角点检测的 Go 自检入口(与 python/harris_check.py 同题)。
//
// 断言挑「解析性/结构性」结论:闭式响应、R<0 的上界、角点-边缘-平坦三分、
// λmin 的半正定性、对比度的 4 次方缩放、响应平台与簇合并、旋转不变/尺度不敏感。
package main

import (
	"fmt"
	"math"
	"os"
)

var total, passed int
var fails []string

func check(name string, cond bool, detail string) {
	total++
	if cond {
		passed++
		fmt.Printf("  [PASS] %s  %s\n", name, detail)
		return
	}
	fails = append(fails, name)
	fmt.Printf("  [FAIL] %s  %s\n", name, detail)
}

// rMaxForK 是 R > 0 的解析上界:r < (1-2k+sqrt(1-4k)) / (2k),r = λ1/λ2 >= 1。
func rMaxForK(k float64) float64 {
	return (1 - 2*k + math.Sqrt(1-4*k)) / (2 * k)
}

func main() {
	fmt.Println("== A. 结构张量的闭式响应 ==")
	check("R(λ=(4,4)) == 13.44", math.Abs(harrisR(4, 0, 4, 0.04)-13.44) < 1e-9,
		fmt.Sprintf("%.4f", harrisR(4, 0, 4, 0.04)))
	check("R(λ=(4,1)) == 3.0", math.Abs(harrisR(4, 0, 1, 0.04)-3.0) < 1e-9,
		fmt.Sprintf("%.4f", harrisR(4, 0, 1, 0.04)))
	check("R(λ=(100,1)) < 0(强边缘)", harrisR(100, 0, 1, 0.04) < 0,
		fmt.Sprintf("%.4f", harrisR(100, 0, 1, 0.04)))
	l1, l2 := eigenvalues(4, 0, 1)
	check("特征值闭式解 (4,1)", math.Abs(l1-4) < 1e-12 && math.Abs(l2-1) < 1e-12,
		fmt.Sprintf("(%.1f,%.1f)", l1, l2))

	fmt.Println("\n== B. R<0 的解析边界与 k 的作用 ==")
	check("k=0.04 的 r_max ≈ 22.9564", math.Abs(rMaxForK(0.04)-22.956439) < 1e-5,
		fmt.Sprintf("%.6f", rMaxForK(0.04)))
	check("r=22 -> R>0,r=23 -> R<0", harrisR(22, 0, 1, 0.04) > 0 && harrisR(23, 0, 1, 0.04) < 0,
		fmt.Sprintf("%.4f / %.4f", harrisR(22, 0, 1, 0.04), harrisR(23, 0, 1, 0.04)))
	check("k 越大对边缘抑制越强", rMaxForK(0.06) < rMaxForK(0.04) && rMaxForK(0.04) < rMaxForK(0.02),
		fmt.Sprintf("%.4f < %.4f < %.4f", rMaxForK(0.06), rMaxForK(0.04), rMaxForK(0.02)))
	check("r=20:k=0.04 判角点、k=0.06 判边缘",
		harrisR(20, 0, 1, 0.04) > 0 && harrisR(20, 0, 1, 0.06) < 0,
		fmt.Sprintf("%.3f vs %.3f", harrisR(20, 0, 1, 0.04), harrisR(20, 0, 1, 0.06)))

	fmt.Println("\n== C. 角点 / 边缘 / 平坦三分 ==")
	img := checkerboard(33, 200)
	resp := responseImage(img, 2, 0.04, "harris")
	check("角点响应 > 0", resp[16][16] > 0, fmt.Sprintf("%.4g", resp[16][16]))
	check("边缘响应 < 0", resp[8][16] < 0, fmt.Sprintf("%.4g", resp[8][16]))
	check("平坦区响应 == 0", resp[4][4] == 0, fmt.Sprintf("%g", resp[4][4]))
	check("响应图最大值出现在角点", peakOf(resp) == resp[16][16], fmt.Sprintf("%.4g", peakOf(resp)))
	ml := minLambda(img, 2)
	check("λmin 全图 >= 0(结构张量半正定)", ml >= 0, fmt.Sprintf("min λ2=%.3e", ml))

	fmt.Println("\n== D. Shi-Tomasi 与 Harris 判据不等价 ==")
	st := responseImage(img, 2, 0.04, "shi_tomasi")
	check("λ=(100,1):Harris R<0 而 λmin=1>0", harrisR(100, 0, 1, 0.04) < 0 && shiTomasi(100, 0, 1) > 0,
		fmt.Sprintf("R=%.3f λmin=1.0", harrisR(100, 0, 1, 0.04)))
	check("理想边缘上 λmin == 0", st[8][16] == 0, fmt.Sprintf("%g", st[8][16]))
	check("角点上 λmin > 0", st[16][16] > 0, fmt.Sprintf("%.4g", st[16][16]))

	fmt.Println("\n== E. 响应随对比度的 4 次方缩放 ==")
	img2 := make([][]float64, len(img))
	for y := range img {
		img2[y] = make([]float64, len(img[y]))
		for x := range img[y] {
			img2[y][x] = img[y][x] * 2
		}
	}
	resp2 := responseImage(img2, 2, 0.04, "harris")
	ratio := resp2[16][16] / resp[16][16]
	check("对比度 x2 -> 响应 x16", math.Abs(ratio-16) < 1e-9, fmt.Sprintf("ratio=%.12f", ratio))
	check("相对阈值在缩放后给出同样的簇数",
		len(clusterCandidates(resp, 0.01)) == len(clusterCandidates(resp2, 0.01)), "")
	tFixed := 1.2 * resp[16][16]
	c1, c2 := 0, 0
	for y := range resp {
		for x := range resp[y] {
			if resp[y][x] > tFixed {
				c1++
			}
			if resp2[y][x] > tFixed {
				c2++
			}
		}
	}
	check("固定绝对阈值不可移植(原图零检出、亮图 36 个)", c1 == 0 && c2 > 0,
		fmt.Sprintf("%d vs %d 个像素", c1, c2))

	fmt.Println("\n== F. blockSize 与响应平台 ==")
	rs := make([]float64, 0)
	for _, b := range []int{1, 2, 5, 8} {
		rs = append(rs, responseImage(img, b, 0.04, "harris")[16][16])
	}
	check("归一化权重下 blockSize 越大响应越小", rs[0] > rs[1] && rs[1] > rs[2] && rs[2] > rs[3],
		fmt.Sprintf("%.4g > %.4g > %.4g > %.4g", rs[0], rs[1], rs[2], rs[3]))
	naive := findCorners(resp, 0.01, 1)
	check("朴素 NMS 在理想角点上留下 16 个候选(4x4 平台)", len(naive) == 16,
		fmt.Sprintf("%d 个", len(naive)))
	cl := clusterCandidates(resp, 0.01)
	check("8 连通合并后只剩 1 个簇", len(cl) == 1, fmt.Sprintf("%d 个", len(cl)))
	check("0.01*max 下该簇含 36 个像素", cl[0][3] == 36, fmt.Sprintf("%.0f 像素", cl[0][3]))
	cl99 := clusterCandidates(resp, 0.99)
	check("阈值抬到 0.99*max 后只剩 16 个平台像素", cl99[0][3] == 16, fmt.Sprintf("%.0f 像素", cl99[0][3]))
	check("簇质心 (15.5,15.5),比边界像素 (16,16) 偏半像素",
		math.Abs(cl[0][0]-15.5) < 1e-12 && math.Abs(cl[0][1]-15.5) < 1e-12,
		fmt.Sprintf("(%.1f,%.1f)", cl[0][0], cl[0][1]))

	fmt.Println("\n== G. 旋转不变 / 尺度不敏感 ==")
	lc := lCorner(16, 8, 200)
	rl := responseImage(lc, 2, 0.04, "harris")
	rr := responseImage(rot90(lc), 2, 0.04, "harris")
	n := len(lc)
	worst := 0.0
	for y := 0; y < n; y++ {
		for x := 0; x < n; x++ {
			d := math.Abs(rr[y][n-1-x] - rl[y][x])
			if d > worst {
				worst = d
			}
		}
	}
	check("90 度旋转后响应逐像素一致", worst < 1e-9, fmt.Sprintf("max diff %.3e", worst))
	check("L 角上恰 1 个簇", len(clusterCandidates(rl, 0.01)) == 1, "")
	cu := clusterCandidates(responseImage(upscale(lc, 4), 2, 0.04, "harris"), 0.01)
	check("同 blockSize 下 4x 放大后簇数变化(尺度不敏感)", len(cu) != 1,
		fmt.Sprintf("%d 个", len(cu)))
	check("放大后的响应峰不落在放大后的原位置 (32,32)",
		int(cu[0][0]) != 32 || int(cu[0][1]) != 32,
		fmt.Sprintf("argmax=(%.0f,%.0f)", cu[0][0], cu[0][1]))

	fmt.Printf("\n结果:%d/%d 通过", passed, total)
	if len(fails) > 0 {
		fmt.Printf(",失败:%v\n", fails)
		os.Exit(1)
	}
	fmt.Println()
}
