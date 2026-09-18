// YOLO 锚框:尺寸 IoU 距离、k-means 聚类(d = 1 - IOU)、手工锚框基线。
//
// 口径来自《YOLO9000: Better, Faster, Stronger》(arXiv:1612.08242)§2.2 Dimension Clusters:
// 锚框用 k-means 选,距离 d(box, centroid) = 1 - IOU 而非欧氏距离
// (同样差 10 像素,对大框是噪声、对小框是灾难);论文报告 k=5 时 avg IOU 61.0,
// 与 Faster R-CNN 手工 9 个锚框(60.9)相当。
package main

import "math"

// AnchorDistance YOLO9000 的 k-means 距离 d(box, centroid) = 1 - IOU。
func AnchorDistance(a, b [2]float64) float64 { return 1.0 - ShapeIoU(a, b) }

// AvgIoU 每个框与"最接近它的锚框"的 IoU 平均值(k-means 的目标函数)。
func AvgIoU(boxes, anchors [][2]float64) float64 {
	if len(boxes) == 0 {
		return 0.0
	}
	total := 0.0
	for _, b := range boxes {
		best := 0.0
		for _, a := range anchors {
			if v := ShapeIoU(b, a); v > best {
				best = v
			}
		}
		total += best
	}
	return total / float64(len(boxes))
}

// lcgNext 确定性线性同余发生器,保证与 Python 侧逐位一致(不用 math/rand)。
//
// 递推 state = (1103515245·state + 12345) mod 2^31;乘数×state 最大约 2.37e18,
// 不会溢出 int64,故与 Python 的大整数取模结果完全相同。
func lcgNext(state *int64) int64 {
	*state = (*state*1103515245 + 12345) % (1 << 31)
	return *state
}

// lcgFloat 把 LCG 状态映射到 [0, 1)。
func lcgFloat(state *int64) float64 {
	return float64(lcgNext(state)) / float64(int64(1)<<31)
}

// KMeansAnchors 用 d = 1 - IOU 做 k-means,k 个质心即 k 个锚框尺寸。
func KMeansAnchors(boxes [][2]float64, k, iters int, seed int64) [][2]float64 {
	if len(boxes) == 0 {
		return nil
	}
	state := seed
	picked := []int{}
	for len(picked) < k && len(picked) < len(boxes) {
		i := int(lcgNext(&state) % int64(len(boxes)))
		dup := false
		for _, p := range picked {
			if p == i {
				dup = true
			}
		}
		if !dup {
			picked = append(picked, i)
		}
	}
	centroids := make([][2]float64, len(picked))
	for i, p := range picked {
		centroids[i] = boxes[p]
	}
	for it := 0; it < iters; it++ {
		groups := make([][][2]float64, len(centroids))
		for _, b := range boxes {
			j, bestD := 0, math.MaxFloat64
			for c, ct := range centroids {
				if d := AnchorDistance(b, ct); d < bestD {
					j, bestD = c, d
				}
			}
			groups[j] = append(groups[j], b)
		}
		moved := false
		for j, g := range groups {
			if len(g) == 0 {
				continue
			}
			w, h := 0.0, 0.0
			for _, b := range g {
				w += b[0]
				h += b[1]
			}
			w /= float64(len(g))
			h /= float64(len(g))
			if math.Abs(w-centroids[j][0]) > 1e-9 || math.Abs(h-centroids[j][1]) > 1e-9 {
				moved = true
			}
			centroids[j] = [2]float64{w, h}
		}
		if !moved {
			break
		}
	}
	return centroids
}

// HandPickedAnchors 手工锚框基线:Faster R-CNN / RPN 的 3 尺度 × 3 宽高比 = 9 个锚框。
// 面积 A、宽高比 r 的锚框为 w = √(A·r),h = √(A/r)。
func HandPickedAnchors(areas, ratios []float64) [][2]float64 {
	out := [][2]float64{}
	for _, a := range areas {
		for _, r := range ratios {
			out = append(out, [2]float64{math.Sqrt(a * r), math.Sqrt(a / r)})
		}
	}
	return out
}
