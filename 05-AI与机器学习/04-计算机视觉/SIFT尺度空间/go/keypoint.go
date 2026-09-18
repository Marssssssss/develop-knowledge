// SIFT 第 4 步后半:用 2x2 Hessian 剔除边缘响应 + 三级筛选总入口。
//
// 论文口径(Lowe IJCV 2004 §4):
//   - DoG 在边缘上也有强响应,但位置沿边缘方向不稳定,必须剔除;
//   - 主曲率由 2x2 Hessian H = [[Dxx, Dxy], [Dxy, Dyy]] 给出。令 α = 较大特征值、β = 较小特征值,
//     Tr(H) = Dxx + Dyy = α + β,Det(H) = Dxx·Dyy - Dxy² = αβ;
//   - 令 r = α/β,则 Tr(H)²/Det(H) = (r+1)²/r,**只依赖比值 r**,无需显式求特征值;
//   - (r+1)²/r 在 r = 1(两特征值相等)取最小值 4,随 r 增大而增大;论文取 r = 10 → 阈值 12.1;
//   - 故判据为 Tr(H)²/Det(H) < (r+1)²/r 才保留;Det <= 0(曲率异号)直接剔除。
package main

// PrincipalCurvatureRatio 返回 Tr(H)²/Det(H) = (r+1)²/r;Det <= 0(曲率异号)时 ok = false。
func PrincipalCurvatureRatio(hxx, hxy, hyy float64) (float64, bool) {
	tr := hxx + hyy
	det := hxx*hyy - hxy*hxy
	if det <= 0.0 {
		return 0.0, false
	}
	return tr * tr / det, true
}

// EdgeRatioLimit 论文判据阈值 (r+1)²/r;r=10 时为 12.1。
func EdgeRatioLimit(r float64) float64 { return (r + 1.0) * (r + 1.0) / r }

// IsEdgeResponse 返回 true 表示应作为边缘响应剔除。
func IsEdgeResponse(hxx, hxy, hyy, r float64) bool {
	ratio, ok := PrincipalCurvatureRatio(hxx, hxy, hyy)
	if !ok {
		return true
	}
	return ratio >= EdgeRatioLimit(r)
}

// HessianAt 返回 D 的 2x2 Hessian 分量 (hxx, hxy, hyy)(论文式 (4))。
func HessianAt(vol [][][]float64, s, y, x int) [3]float64 {
	hxx := vol[s][y][x+1] - 2.0*vol[s][y][x] + vol[s][y][x-1]
	hyy := vol[s][y+1][x] - 2.0*vol[s][y][x] + vol[s][y-1][x]
	hxy := (vol[s][y+1][x+1] - vol[s][y+1][x-1] - vol[s][y-1][x+1] + vol[s][y-1][x-1]) * 0.25
	return [3]float64{hxx, hxy, hyy}
}

// Keypoint 经三级筛选后的极值点及其判定结果。
type Keypoint struct {
	Octave, S, Y, X int
	Value           float64
	Status          string
}

// Detect 对单个 octave 的 DoG 体积做完整筛选:定位 → 对比度 → 边缘。
func Detect(vol [][][]float64, contrastThreshold, r float64) []Keypoint {
	out := []Keypoint{}
	for _, e := range FindExtrema([][][][]float64{vol}) {
		loc := Localize(vol, e.S, e.Y, e.X, contrastThreshold)
		switch {
		case !loc.OK:
			out = append(out, Keypoint{e.Octave, e.S, e.Y, e.X, e.Value, "singular"})
		case loc.NeedResample:
			out = append(out, Keypoint{e.Octave, e.S, e.Y, e.X, e.Value, "offset>0.5"})
		case !loc.ContrastOK:
			out = append(out, Keypoint{e.Octave, e.S, e.Y, e.X, e.Value, "low_contrast"})
		default:
			h := HessianAt(vol, e.S, e.Y, e.X)
			if IsEdgeResponse(h[0], h[1], h[2], r) {
				out = append(out, Keypoint{e.Octave, e.S, e.Y, e.X, e.Value, "edge"})
			} else {
				out = append(out, Keypoint{e.Octave, e.S, e.Y, e.X, e.Value, "kept"})
			}
		}
	}
	return out
}
