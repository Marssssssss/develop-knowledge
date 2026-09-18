// YOLO 检测头自检(与 python/yolo_head_check.py 一一对应)。
//
// 运行:cd go && go run .
// 注意:本机未安装 Go 工具链,本组文件未经编译器验证,已用 _docs/tools/bracket_check.py
// 与 syntax_sanity.py 检查,并逐处人工核对函数签名与实参个数。
//
// A 锚框解码 / B 网格量化 / C 正负忽略分配 / D 分类器口径 / E k-means 锚框 / F 损失与忽略规则。
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

func containsInt(xs []int, v int) bool {
	for _, x := range xs {
		if x == v {
			return true
		}
	}
	return false
}

// syntheticBoxes 三个已知尺寸簇的合成框,用于检验 k-means 能否把它们还原出来。
func syntheticBoxes(clusters [][2]float64, per int, jitter float64) [][2]float64 {
	state := int64(12345)
	boxes := [][2]float64{}
	for _, c := range clusters {
		for i := 0; i < per; i++ {
			w := c[0] * (1.0 + jitter*(2.0*lcgFloat(&state)-1.0))
			h := c[1] * (1.0 + jitter*(2.0*lcgFloat(&state)-1.0))
			boxes = append(boxes, [2]float64{w, h})
		}
	}
	return boxes
}

func checkDecode() {
	fmt.Println("== A. 锚框解码:sigmoid 中心 + 指数尺寸 ==")
	check("σ(0) == 0.5", Sigmoid(0.0) == 0.5, "0.5")
	strict := true
	for _, x := range []float64{-36, -5, -1, 0, 1, 5, 36} {
		if s := Sigmoid(x); s <= 0.0 || s >= 1.0 {
			strict = false
		}
	}
	check("σ 在 |x| <= 36 时严格落在 (0,1) -> 中心永远落在当前格内", strict, "")
	check("坑:正向上饱和更早 —— σ(37) 在 float64 下就是精确的 1.0(中心可落到格边界)",
		Sigmoid(36.0) < 1.0 && Sigmoid(37.0) == 1.0,
		fmt.Sprintf("σ(36)=%v σ(37)=%v", Sigmoid(36.0), Sigmoid(37.0)))
	check("负向不对称:σ(-37) 仍是可表示的 e^-37,要到指数下溢才归零",
		Sigmoid(-37.0) > 0.0 && Sigmoid(-1000.0) == 0.0,
		fmt.Sprintf("σ(-37)=%.3e", Sigmoid(-37.0)))
	sym := 0.0
	for _, x := range []float64{0.3, 1.0, 4.0} {
		if d := math.Abs(Sigmoid(-x) - (1.0 - Sigmoid(x))); d > sym {
			sym = d
		}
	}
	check("σ(-x) == 1 - σ(x)", sym < 1e-15, fmt.Sprintf("max diff %.2e", sym))
	box := DecodeBox([4]float64{0, 0, 0, 0}, [2]float64{116, 90}, [2]int{5, 7}, 32)
	check("t 全零时中心落在格的几何中心(5.5·32, 7.5·32)",
		box == [4]float64{176, 240, 116, 90}, fmt.Sprintf("%v", box))
	b1 := DecodeBox([4]float64{0, 0, 1, 0}, [2]float64{116, 90}, [2]int{0, 0}, 32)
	check("tw = 1 -> 宽度放大 e 倍", math.Abs(b1[2]-116.0*math.E) < 1e-12,
		fmt.Sprintf("%.6f vs %.6f", b1[2], 116.0*math.E))
	rt := true
	for _, tb := range [][4]float64{{191, 245, 120, 88}, {16, 16, 116, 90}} {
		t, cell := EncodeBox(tb, [2]float64{116, 90}, 32)
		back := DecodeBox(t, [2]float64{116, 90}, cell, 32)
		for i := 0; i < 4; i++ {
			if math.Abs(back[i]-tb[i]) >= 1e-9 {
				rt = false
			}
		}
	}
	check("encode -> decode 往返无损(误差 < 1e-9)", rt, "")
	inside := true
	for _, tx := range []float64{-20, 0, 20} {
		cx := DecodeBox([4]float64{tx, 0, 0, 0}, [2]float64{116, 90}, [2]int{5, 0}, 32)[0]
		if cx <= 5*32 || cx >= 6*32 {
			inside = false
		}
	}
	check("任意 tx 都无法把中心推出当前格(这是 sigmoid 归一化的目的)", inside, "")
	lt := 0.0
	for _, x := range []float64{-5, -1, 0, 1, 5} {
		if d := math.Abs(Logit(Sigmoid(x)) - x); d > lt {
			lt = d
		}
	}
	check("logit 是 sigmoid 的反函数(编码用)", lt < 1e-9, fmt.Sprintf("max diff %.2e", lt))
}

func checkGrid() {
	fmt.Println("\n== B. 网格量化误差 ==")
	check("最大量化误差 = 半个 stride = 16 px", MaxQuantizationError(32.0) == 16.0, "")
	q := []float64{}
	for _, s := range DefaultStrides {
		q = append(q, MaxQuantizationError(s))
	}
	check("三个尺度的量化误差 16 / 8 / 4 px",
		fmt.Sprintf("%v", q) == "[16 8 4]", fmt.Sprintf("%v", q))
	cell := [2]int{int(191 / 32), int(245 / 32)}
	got := DecodeBox([4]float64{0, 0, 0, 0}, [2]float64{116, 90}, cell, 32)
	errX, errY := math.Abs(191-got[0]), math.Abs(245-got[1])
	check("真实中心 (191,245) 被量化到格 (5,7) 的中心,误差 (15,5) <= (16,16)",
		cell == [2]int{5, 7} && errX == 15.0 && errY == 5.0,
		fmt.Sprintf("cell=%v err=(%.0f,%.0f)", cell, errX, errY))
	check("三尺度共存时总预测数 = 3·(13² + 26² + 52²) = 10647",
		TotalPredictions([]int{13, 26, 52}, AnchorsPerCell) == 10647,
		fmt.Sprintf("%d", TotalPredictions([]int{13, 26, 52}, AnchorsPerCell)))
	only13 := TotalPredictions([]int{13}, AnchorsPerCell)
	check("去掉两个细尺度后预测数只剩 507(少 21 倍)",
		only13 == 507 && math.Abs(10647.0/float64(only13)-21.0) < 1e-12, fmt.Sprintf("%d", only13))
	shp := HeadTensorShape(13, AnchorsPerCell, VecDim)
	check("头部张量形状 13x13x3x85 = 43095",
		shp == [4]int{13, 13, 3, 85} && shp[0]*shp[1]*shp[2]*shp[3] == 43095,
		fmt.Sprintf("%v", shp))
	c2 := GridCells(2)
	check("格坐标按行优先枚举(与特征图内存顺序一致)",
		fmt.Sprintf("%v", c2) == "[[0 0] [1 0] [0 1] [1 1]]", fmt.Sprintf("%v", c2))
}

func checkAssign() {
	fmt.Println("\n== C. 锚框分配:负责 / 忽略 / 负样本 ==")
	check("同尺寸形状 IoU == 1",
		ShapeIoU([2]float64{10, 10}, [2]float64{10, 10}) == 1.0, "")
	check("同心 (10,10) 与 (20,20) 的 IoU == 0.25(面积比 1:4)",
		ShapeIoU([2]float64{10, 10}, [2]float64{20, 20}) == 0.25, "0.25")
	check("同心 (10,10) 与 (10,20) 的 IoU == 0.5(边界值)",
		ShapeIoU([2]float64{10, 10}, [2]float64{10, 20}) == 0.5, "0.5")
	r := AssignAnchors([2]float64{100, 100}, [][2]float64{{100, 100}, {80, 90}, {50, 200}},
		IgnoreThresh)
	check("IoU 最大的锚框负责该目标", r.Responsible == 0 && r.IOUs[0] == 1.0,
		fmt.Sprintf("ious=[%.4f %.4f %.4f]", r.IOUs[0], r.IOUs[1], r.IOUs[2]))
	check("非最优但 IoU 0.72 > 0.5 -> 进入忽略集",
		fmt.Sprintf("%v", r.Ignore) == "[1]" && math.Abs(r.IOUs[1]-0.72) < 1e-12,
		fmt.Sprintf("ignore=%v", r.Ignore))
	check("IoU 0.333 < 0.5 -> 作为负样本参与惩罚",
		fmt.Sprintf("%v", r.Negative) == "[2]" && math.Abs(r.IOUs[2]-1.0/3.0) < 1e-12,
		fmt.Sprintf("negative=%v", r.Negative))
	check("负责锚框不落在忽略集/负样本集中",
		!containsInt(r.Ignore, r.Responsible) && !containsInt(r.Negative, r.Responsible), "")
	b := AssignAnchors([2]float64{10, 10}, [][2]float64{{10, 10}, {10, 20}}, IgnoreThresh)
	check("IoU 恰为 0.5 不忽略(论文判据是 strictly > 0.5,须用 > 而非 >=)",
		len(b.Ignore) == 0 && fmt.Sprintf("%v", b.Negative) == "[1]",
		fmt.Sprintf("ignore=%v", b.Ignore))
	all := append(append(append([]int{}, r.Ignore...), r.Negative...), r.Responsible)
	seen := map[int]bool{}
	dup := false
	for _, v := range all {
		if seen[v] {
			dup = true
		}
		seen[v] = true
	}
	check("忽略集与负样本集互斥且并集为全部锚框", len(all) == 3 && !dup, "")
}

func checkClassifier() {
	fmt.Println("\n== D. 分类器口径:softmax vs 独立 logistic ==")
	sm := Softmax([]float64{8, 6})
	check("softmax([8,6]) 归一化为 1", math.Abs(sm[0]+sm[1]-1.0) < 1e-15,
		fmt.Sprintf("sum=%.16f", sm[0]+sm[1]))
	check("softmax([8,6]) = (0.880797, 0.119203)",
		math.Abs(sm[0]-0.8807970779778823) < 1e-12 && math.Abs(sm[1]-0.1192029220221176) < 1e-12,
		fmt.Sprintf("[%.6f %.6f]", sm[0], sm[1]))
	lg := []float64{Sigmoid(8.0), Sigmoid(6.0)}
	check("同一组 logits 下两个独立 logistic 输出都 > 0.99(允许重叠标签同时为真)",
		lg[0] > 0.99 && lg[1] > 0.99, fmt.Sprintf("[%.6f %.6f]", lg[0], lg[1]))
	check("softmax 把第二名压到 0.119 < 0.5(强制互斥,重叠标签无法表达)", sm[1] < 0.5,
		fmt.Sprintf("p2=%.6f", sm[1]))
	inv := Softmax([]float64{108, 106})
	check("softmax 平移不变(先减 max 保证数值稳定)",
		math.Abs(inv[0]-sm[0]) < 1e-15 && math.Abs(inv[1]-sm[1]) < 1e-15, "")
	huge := Softmax([]float64{1000, 1000})
	check("softmax([1000,1000]) 不溢出,得 (0.5, 0.5)", huge[0] == 0.5 && huge[1] == 0.5, "")
	lSoft := ClassLossSoftmax([]float64{8, 6}, 1)
	lLogi := ClassLossLogistic([]float64{8, 6}, []float64{1, 1})
	check("同一 logits:softmax 交叉熵 2.13(第二名被判错)而 logistic BCE 仅 0.0028",
		math.Abs(lSoft-2.1269280110429727) < 1e-12 && lLogi < 0.01,
		fmt.Sprintf("soft=%.6f logi=%.6f", lSoft, lLogi))
}

func checkKMeans() {
	fmt.Println("\n== E. k-means 锚框聚类(d = 1 - IOU) ==")
	check("相同尺寸的 k-means 距离为 0",
		AnchorDistance([2]float64{50, 50}, [2]float64{50, 50}) == 0.0, "")
	check("尺寸差一倍时距离 = 1 - 0.25 = 0.75",
		math.Abs(AnchorDistance([2]float64{10, 10}, [2]float64{20, 20})-0.75) < 1e-15, "0.75")
	check("形状越接近距离越小(单调性,代替欧氏距离的动机)",
		AnchorDistance([2]float64{100, 100}, [2]float64{110, 110})
			< AnchorDistance([2]float64{100, 100}, [2]float64{200, 200}), "")
	truth := [][2]float64{{40, 40}, {120, 60}, {200, 240}}
	boxes := syntheticBoxes(truth, 100, 0.12)
	km1 := KMeansAnchors(boxes, 1, 50, 12345)
	km3 := KMeansAnchors(boxes, 3, 50, 12345)
	i1, i3 := AvgIoU(boxes, km1), AvgIoU(boxes, km3)
	oracle := AvgIoU(boxes, truth)
	check("k=3 达到 oracle 上界的 98% 以上(oracle = 直接用三个真尺寸当锚框)",
		i3 > 0.98*oracle && i3 > i1+0.2,
		fmt.Sprintf("oracle=%.4f k=1=%.4f k=3=%.4f", oracle, i1, i3))
	matched := 0
	for _, c := range km3 {
		best := math.MaxFloat64
		for _, t := range truth {
			e := math.Max(math.Abs(c[0]-t[0])/t[0], math.Abs(c[1]-t[1])/t[1])
			if e < best {
				best = e
			}
		}
		if best < 0.10 {
			matched++
		}
	}
	check("三个锚框各自命中一个真值簇(相对误差 < 10%,与质心顺序无关)", matched == 3,
		fmt.Sprintf("%d/3 命中", matched))
	curve := []float64{}
	mono := true
	for _, k := range []int{1, 2, 3, 5, 9} {
		v := AvgIoU(boxes, KMeansAnchors(boxes, k, 50, 12345))
		if len(curve) > 0 && curve[len(curve)-1] > v+1e-9 {
			mono = false
		}
		curve = append(curve, v)
	}
	check("avgIoU 随 k 单调不减(锚框越多越能覆盖真实尺寸)", mono,
		fmt.Sprintf("%.4f %.4f %.4f %.4f %.4f", curve[0], curve[1], curve[2], curve[3], curve[4]))
	hp := HandPickedAnchors([]float64{128 * 128, 256 * 256, 512 * 512}, []float64{1.0, 0.5, 2.0})
	ihp := AvgIoU(boxes, hp)
	i5 := AvgIoU(boxes, KMeansAnchors(boxes, 5, 50, 12345))
	check("同一批框上:k-means 5 个锚框优于 RPN 手工 9 个(论文结论在合成数据上的复现)",
		i5 > ihp && len(hp) == 9, fmt.Sprintf("kmeans k=5 %.4f vs hand-picked 9 %.4f", i5, ihp))
}

func checkLoss() {
	fmt.Println("\n== F. 损失与忽略规则的必要性 ==")
	check("BCE 在预测正确时为 0", BCE(1.0, 1.0) < 1e-11 && BCE(0.0, 0.0) < 1e-11, "")
	check("BCE(p=0.5, y=1) == ln2", math.Abs(BCE(0.5, 1.0)-math.Log(2.0)) < 1e-15,
		fmt.Sprintf("%.16f", BCE(0.5, 1.0)))
	preds, targets := []float64{0.9, 0.0, 0.1}, []float64{1.0, 1.0, 0.0}
	w1 := ObjectnessLoss(preds, targets, []int{1})
	w0 := ObjectnessLoss(preds, targets, nil)
	check("被忽略的锚框贡献 0,总损失只有另两个锚框的量级(0.2107)",
		math.Abs(w1-0.21072103131565256) < 1e-12, fmt.Sprintf("%.6f", w1))
	check("若把'自信但错误的'锚框当负样本,单个样本就把损失放大 100 倍以上",
		w0 > 100*w1, fmt.Sprintf("%.4f vs %.6f", w0, w1))
	check("类别 BCE 逐类独立:抬高一个'目标为 0'的类的 logit,损失单调增加",
		ClassLossLogistic([]float64{8, -6}, []float64{1, 0})
			< ClassLossLogistic([]float64{8, 6}, []float64{1, 0}),
		fmt.Sprintf("%.6f -> %.6f", ClassLossLogistic([]float64{8, -6}, []float64{1, 0}),
			ClassLossLogistic([]float64{8, 6}, []float64{1, 0})))
}

func main() {
	checkDecode()
	checkGrid()
	checkAssign()
	checkClassifier()
	checkKMeans()
	checkLoss()
	fmt.Printf("\n结果:%d/%d 通过", passed, total)
	if len(fails) > 0 {
		fmt.Printf(",失败:%v", fails)
		os.Exit(1)
	}
	fmt.Println()
}
