// YOLO 检测头:锚框解码、多尺度网格、正/负/忽略样本分配、分类器与损失、k-means 锚框聚类。
//
// 权威口径:J.Redmon & A.Farhadi
//   - 《YOLOv3: An Incremental Improvement》(arXiv:1804.02767)§2.1:
//     bx = σ(tx) + cx,by = σ(ty) + cy;bw = pw·e^tw,bh = ph·e^th
//     中心用 sigmoid 归一化进当前格,尺寸用指数缩放锚框;
//     分类用独立 logistic 而非 softmax(softmax 假设类别互斥,而数据集存在重叠标签);
//     与 GT 的 IoU 最大的锚框负责该目标,非最优但 IoU > 0.5 的锚框被忽略(不惩罚)。
//   - 《YOLO9000: Better, Faster, Stronger》(arXiv:1612.08242)§2.2:
//     锚框用 k-means 聚类选,距离 d(box, centroid) = 1 - IOU;
//     论文报告 k=5 时 avg IOU 61.0,与 Faster R-CNN 手工 9 个锚框(60.9)相当。
//
// 与 python/yolo_head.py 一一对应。本文件只实现头部与分配逻辑,不加载权重/数据集。
package main

import "math"

const (
	// BoxDim 每个预测框的 4 个偏移 (tx, ty, tw, th)。
	BoxDim = 4
	// ObjDim objectness 占 1 维。
	ObjDim = 1
	// Classes COCO 的 80 类。
	Classes = 80
	// AnchorsPerCell 每格 3 个锚框。
	AnchorsPerCell = 3
	// VecDim 每格向量长度 4 + 1 + 80 = 85。
	VecDim = BoxDim + ObjDim + Classes
	// IgnoreThresh 论文:非最优但 IoU 超过 0.5 的锚框被忽略。
	IgnoreThresh = 0.5
)

// DefaultStrides 输入 416 时三个尺度的步长(对应 13 / 26 / 52 网格)。
var DefaultStrides = []float64{32, 16, 8}

// Sigmoid logistic 函数;论文用它把中心偏移压进当前格。
func Sigmoid(x float64) float64 {
	if x >= 0 {
		return 1.0 / (1.0 + math.Exp(-x))
	}
	e := math.Exp(x)
	return e / (1.0 + e)
}

// Logit Sigmoid 的反函数,把真实框编码成训练目标。
func Logit(p float64) float64 { return math.Log(p / (1.0 - p)) }

// Softmax 数值稳定实现(先减最大值)。
func Softmax(logits []float64) []float64 {
	m := logits[0]
	for _, v := range logits[1:] {
		if v > m {
			m = v
		}
	}
	ex := make([]float64, len(logits))
	s := 0.0
	for i, v := range logits {
		ex[i] = math.Exp(v - m)
		s += ex[i]
	}
	for i := range ex {
		ex[i] /= s
	}
	return ex
}

// DecodeBox (tx,ty,tw,th) + 锚框形状 + 格坐标 -> 图像坐标系 (cx, cy, w, h)。
func DecodeBox(t [4]float64, anchor [2]float64, cell [2]int, stride float64) [4]float64 {
	cx := (Sigmoid(t[0]) + float64(cell[0])) * stride
	cy := (Sigmoid(t[1]) + float64(cell[1])) * stride
	return [4]float64{cx, cy, anchor[0] * math.Exp(t[2]), anchor[1] * math.Exp(t[3])}
}

// EncodeBox DecodeBox 的逆:真实框 -> (t, 格坐标)。
func EncodeBox(box [4]float64, anchor [2]float64, stride float64) ([4]float64, [2]int) {
	gx := int(box[0] / stride)
	gy := int(box[1] / stride)
	t := [4]float64{
		Logit(box[0]/stride - float64(gx)),
		Logit(box[1]/stride - float64(gy)),
		math.Log(box[2] / anchor[0]),
		math.Log(box[3] / anchor[1]),
	}
	return t, [2]int{gx, gy}
}

// IoU 两个完整框 (cx, cy, w, h) 的交并比。
func IoU(a, b [4]float64) float64 {
	ax1, ay1 := a[0]-a[2]/2.0, a[1]-a[3]/2.0
	ax2, ay2 := a[0]+a[2]/2.0, a[1]+a[3]/2.0
	bx1, by1 := b[0]-b[2]/2.0, b[1]-b[3]/2.0
	bx2, by2 := b[0]+b[2]/2.0, b[1]+b[3]/2.0
	iw := math.Min(ax2, bx2) - math.Max(ax1, bx1)
	ih := math.Min(ay2, by2) - math.Max(ay1, by1)
	if iw <= 0.0 || ih <= 0.0 {
		return 0.0
	}
	inter := iw * ih
	union := a[2]*a[3] + b[2]*b[3] - inter
	if union <= 0.0 {
		return 0.0
	}
	return inter / union
}

// ShapeIoU 两个"尺寸" (w, h) 在同一中心上的 IoU。
func ShapeIoU(a, b [2]float64) float64 {
	return IoU([4]float64{0, 0, a[0], a[1]}, [4]float64{0, 0, b[0], b[1]})
}

// AssignResult 锚框分配结果。
type AssignResult struct {
	Responsible int
	Ignore      []int
	Negative    []int
	IOUs        []float64
}

// AssignAnchors 按形状 IoU 分配 ground truth:最大的负责,非最优但 > 0.5 的忽略,其余为负样本。
//
// 口径标注:论文与 Darknet 实现中这条忽略规则作用在**网络预测框**与 GT 之间(依赖权重);
// 本 demo 不加载权重,退化为作用在**锚框形状**上,判据形式相同但比较对象不同。
func AssignAnchors(gtShape [2]float64, anchors [][2]float64, ignoreThresh float64) AssignResult {
	ious := make([]float64, len(anchors))
	best := 0
	for i, a := range anchors {
		ious[i] = ShapeIoU(gtShape, a)
		if ious[i] > ious[best] {
			best = i
		}
	}
	res := AssignResult{Responsible: best, Ignore: []int{}, Negative: []int{}, IOUs: ious}
	for i := range anchors {
		if i == best {
			continue
		}
		if ious[i] > ignoreThresh {
			res.Ignore = append(res.Ignore, i)
		} else {
			res.Negative = append(res.Negative, i)
		}
	}
	return res
}

// GridCells 按行优先枚举格坐标,与卷积特征图的内存顺序一致。
func GridCells(grid int) [][2]int {
	out := make([][2]int, 0, grid*grid)
	for y := 0; y < grid; y++ {
		for x := 0; x < grid; x++ {
			out = append(out, [2]int{x, y})
		}
	}
	return out
}

// AnchorsPerScale 单个尺度产出的预测框数。
func AnchorsPerScale(grid, nAnchors int) int { return grid * grid * nAnchors }

// TotalPredictions 多尺度头部一共产出多少个预测框。
func TotalPredictions(grids []int, nAnchors int) int {
	total := 0
	for _, g := range grids {
		total += AnchorsPerScale(g, nAnchors)
	}
	return total
}

// HeadTensorShape 头部张量形状 (grid, grid, anchors, vec)。
func HeadTensorShape(grid, nAnchors, vecDim int) [4]int {
	return [4]int{grid, grid, nAnchors, vecDim}
}

// MaxQuantizationError 中心只能落在格内,故最大量化误差是半个 stride。
func MaxQuantizationError(stride float64) float64 { return stride / 2.0 }

// BCE 二元交叉熵,对 0/1 做数值夹紧避免 log(0)。
func BCE(p, y float64) float64 {
	if p < 1e-12 {
		p = 1e-12
	}
	if p > 1.0-1e-12 {
		p = 1.0 - 1e-12
	}
	return -(y*math.Log(p) + (1.0-y)*math.Log(1.0-p))
}

// ClassLossSoftmax softmax + 交叉熵:强制各类概率之和为 1。
func ClassLossSoftmax(logits []float64, target int) float64 {
	return -math.Log(Softmax(logits)[target])
}

// ClassLossLogistic 独立 logistic 分类器:每类各自 BCE,类之间互不竞争。
func ClassLossLogistic(logits, targets []float64) float64 {
	s := 0.0
	for i, z := range logits {
		s += BCE(Sigmoid(z), targets[i])
	}
	return s
}

// ObjectnessLoss objectness 的 BCE 之和;ignored 中的下标直接跳过(论文的忽略语义)。
//
// 这条规则不是可有可无:被忽略的锚框常是"预测得很自信但位置差半个格子"的那些,
// 若按负样本惩罚,单个样本的 BCE 可达 27 量级,会把整个 batch 的梯度带偏。
func ObjectnessLoss(preds, targets []float64, ignored []int) float64 {
	skip := map[int]bool{}
	for _, i := range ignored {
		skip[i] = true
	}
	s := 0.0
	for i := range preds {
		if skip[i] {
			continue
		}
		s += BCE(preds[i], targets[i])
	}
	return s
}

