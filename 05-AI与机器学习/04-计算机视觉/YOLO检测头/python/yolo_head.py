# -*- coding: utf-8 -*-
"""YOLO 检测头:锚框解码、多尺度网格、正/负/忽略样本分配、分类器与损失、k-means 锚框聚类。

权威口径:J.Redmon & A.Farhadi
- 《YOLOv3: An Incremental Improvement》(arXiv:1804.02767)§2.1 Bounding Box Prediction:
  `bx = σ(tx) + cx, by = σ(ty) + cy, bw = pw·e^tw, bh = ph·e^th`
  —— 中心用 **sigmoid 归一化到当前格内**,尺寸用 **指数缩放锚框**;
  以及"我们用**独立的 logistic 分类器**替代 softmax,因为 softmax 假设类别互斥,
  而有些数据集有重叠标签(如 Woman 与 Person)";
  以及正样本分配:"**与 ground truth 的 IoU 最大的锚框**负责该目标",
  "对那些**不是最好但 IoU 超过 0.5** 的锚框**不做任何惩罚**(忽略)"。
- 《YOLO9000: Better, Faster, Stronger》(arXiv:1612.08242)§2.2 Dimension Clusters:
  用 **k-means 聚类** 选锚框,距离用 `d(box, centroid) = 1 - IOU`
  (不用欧氏距离,因为大框的欧氏误差天然更大);论文报告 k=5 时 avg IOU 61.0,
  与 Faster R-CNN 手工选的 9 个锚框(60.9)相当。

本文件不加载任何权重/数据集,只实现并验证**头部与分配逻辑**本身。
"""
import math

# YOLOv3 输出的每一格向量:4 个框偏移 + 1 个 objectness + 80 类 COCO
BOX_DIM = 4
OBJ_DIM = 1
CLASSES = 80
ANCHORS_PER_CELL = 3
VEC_DIM = BOX_DIM + OBJ_DIM + CLASSES          # 85
DEFAULT_STRIDES = (32, 16, 8)                  # 输入 416 -> 13 / 26 / 52
IGNORE_THRESH = 0.5                            # 论文:非最优但 IoU > 0.5 的锚框忽略


def sigmoid(x):
    """logistic 函数;论文用它把中心偏移压进当前格 (0, 1)。"""
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def logit(p):
    """sigmoid 的反函数,用于把真实框编码成训练目标。"""
    return math.log(p / (1.0 - p))


def softmax(logits):
    m = max(logits)
    ex = [math.exp(v - m) for v in logits]
    s = sum(ex)
    return [v / s for v in ex]


def decode_box(t, anchor, cell, stride):
    """(tx, ty, tw, th) + 锚框 + 格坐标 -> 图像坐标系 (cx, cy, w, h)。

    论文式:bx = σ(tx) + cx,by = σ(ty) + cy;bw = pw·e^tw,bh = ph·e^th。
    注意中心要乘 stride(网络在降采样后的特征图上预测),尺寸直接是图像像素单位。
    """
    tx, ty, tw, th = t
    gx, gy = cell
    cx = (sigmoid(tx) + gx) * stride
    cy = (sigmoid(ty) + gy) * stride
    return cx, cy, anchor[0] * math.exp(tw), anchor[1] * math.exp(th)


def encode_box(box, anchor, stride):
    """decode_box 的逆:真实框 -> (tx, ty, tw, th, 格坐标)。"""
    cx, cy, w, h = box
    gx, gy = int(cx // stride), int(cy // stride)
    tx = logit(cx / stride - gx)
    ty = logit(cy / stride - gy)
    return (tx, ty, math.log(w / anchor[0]), math.log(h / anchor[1])), (gx, gy)


def iou(a, b):
    """两个完整框 (cx, cy, w, h) 的 IoU。"""
    ax1, ay1, ax2, ay2 = a[0] - a[2] / 2.0, a[1] - a[3] / 2.0, a[0] + a[2] / 2.0, a[1] + a[3] / 2.0
    bx1, by1, bx2, by2 = b[0] - b[2] / 2.0, b[1] - b[3] / 2.0, b[0] + b[2] / 2.0, b[1] + b[3] / 2.0
    iw = min(ax2, bx2) - max(ax1, bx1)
    ih = min(ay2, by2) - max(ay1, by1)
    if iw <= 0.0 or ih <= 0.0:
        return 0.0
    inter = iw * ih
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0.0 else 0.0


def shape_iou(a, b):
    """两个"尺寸" (w, h) 的 IoU:放在同一中心上做交并比。

    锚框本身没有位置,只有宽高;因此"挑哪个锚框负责"本质上是**形状匹配**问题。
    """
    return iou((0.0, 0.0, a[0], a[1]), (0.0, 0.0, b[0], b[1]))


def assign_anchors(gt_shape, anchor_shapes, ignore_thresh=IGNORE_THRESH):
    """把 ground truth 分配给锚框(按形状 IoU)。

    返回 dict:responsible(负责该目标的锚框下标,恰 1 个)/ ignore(不惩罚的下标)/
    negative(真正参与负样本惩罚的下标)/ ious。

    论文:获得**最大 IoU** 的锚框负责该目标;"非最优但 IoU 超过 0.5"的锚框**被忽略**
    (既不奖励也不惩罚)。

    口径标注:论文与 Darknet 实现中,这条忽略规则作用在**网络预测框**与 GT 之间
    (因此依赖权重);本 demo 不加载任何权重,退化为作用在**锚框形状**上。
    两处判据形式相同(> 0.5),但比较对象不同。
    """
    ious = [shape_iou(gt_shape, a) for a in anchor_shapes]
    best = max(range(len(anchor_shapes)), key=lambda i: ious[i])
    ignore = [i for i in range(len(anchor_shapes)) if i != best and ious[i] > ignore_thresh]
    negative = [i for i in range(len(anchor_shapes)) if i != best and i not in ignore]
    return {"responsible": best, "ignore": ignore, "negative": negative, "ious": ious}


def grid_cells(grid):
    """按行优先枚举格坐标,与卷积特征图的内存顺序一致。"""
    return [(x, y) for y in range(grid) for x in range(grid)]


def anchors_per_scale(grid, n_anchors=ANCHORS_PER_CELL):
    return grid * grid * n_anchors


def total_predictions(grids=(13, 26, 52), n_anchors=ANCHORS_PER_CELL):
    """三尺度头部一共产出多少个预测框。"""
    return sum(anchors_per_scale(g, n_anchors) for g in grids)


def head_tensor_shape(grid, n_anchors=ANCHORS_PER_CELL, vec_dim=VEC_DIM):
    return (grid, grid, n_anchors, vec_dim)


def max_quantization_error(stride):
    """中心只能落在格内,故最大量化误差是半个格子。"""
    return stride / 2.0


def bce(p, y):
    """二元交叉熵 -[y·ln p + (1-y)·ln(1-p)],对 0/1 做数值夹紧避免 log(0)。"""
    p = min(max(p, 1e-12), 1.0 - 1e-12)
    return -(y * math.log(p) + (1.0 - y) * math.log(1.0 - p))


def class_loss_softmax(logits, target):
    """softmax + 交叉熵:强制 Σp = 1,一个类高了别的必须低。"""
    return -math.log(softmax(logits)[target])


def class_loss_logistic(logits, targets):
    """独立 logistic 分类器:每类各自做 BCE,类之间互不竞争(论文用于重叠标签)。"""
    return sum(bce(sigmoid(z), y) for z, y in zip(logits, targets))


def objectness_loss(preds, targets, ignored=()):
    """objectness 的 BCE 之和;`ignored` 中的下标直接跳过(论文的"忽略"语义)。

    这条规则不是可有可无的:被忽略的锚框往往是"预测得很自信但位置差了半个格子"的那些,
    若按负样本惩罚,单个样本的 BCE 可达 27 量级,会把整个 batch 的梯度带偏。
    """
    return sum(bce(p, y) for i, (p, y) in enumerate(zip(preds, targets)) if i not in ignored)


def anchor_distance(a, b):
    """YOLO9000 的 k-means 距离 d(box, centroid) = 1 - IOU。

    为什么不用欧氏距离:同样差 10 个像素,对大框只是噪声、对小框是灾难,
    欧氏距离会把两类误差等同看待,聚出来的锚框偏向大框。
    """
    return 1.0 - shape_iou(a, b)


def avg_iou(box_shapes, anchor_shapes):
    """每个框与"最接近它的锚框"的 IoU 平均值(k-means 的目标函数)。"""
    if not box_shapes:
        return 0.0
    return sum(max(shape_iou(b, a) for a in anchor_shapes) for b in box_shapes) / len(box_shapes)


def kmeans_anchors(boxes, k, iters=50, seed=12345):
    """用 d = 1 - IOU 做 k-means;k 个质心即 k 个锚框尺寸。

    初值用确定性 LCG 抽 k 个不同样本(不用 random 模块,保证跨语言可复现)。
    """
    if not boxes:
        return []
    state = seed
    picked = []

    def nxt(n):
        nonlocal state
        state = (1103515245 * state + 12345) % (2 ** 31)
        return state % n

    while len(picked) < min(k, len(boxes)):
        i = nxt(len(boxes))
        if i not in picked:
            picked.append(i)
    centroids = [boxes[i] for i in picked]
    for _ in range(iters):
        groups = [[] for _ in centroids]
        for b in boxes:
            j = min(range(len(centroids)),
                    key=lambda c: anchor_distance(b, centroids[c]))
            groups[j].append(b)
        moved = False
        for j, g in enumerate(groups):
            if not g:
                continue
            w = sum(b[0] for b in g) / len(g)
            h = sum(b[1] for b in g) / len(g)
            if abs(w - centroids[j][0]) > 1e-9 or abs(h - centroids[j][1]) > 1e-9:
                moved = True
            centroids[j] = (w, h)
        if not moved:
            break
    return centroids


def hand_picked_anchors(areas=(128.0 ** 2, 256.0 ** 2, 512.0 ** 2), ratios=(1.0, 0.5, 2.0)):
    """手工锚框基线:Faster R-CNN / RPN 的经典方案 —— 3 个尺度 × 3 个宽高比 = 9 个锚框。

    对一个面积为 A、宽高比为 r 的锚框:w = √(A·r),h = √(A/r)。
    论文在 VOC 2007 上报告这 9 个手选锚框的 avg IOU 是 60.9,而 k-means 用 5 个就到 61.0。
    """
    out = []
    for a in areas:
        for r in ratios:
            out.append((math.sqrt(a * r), math.sqrt(a / r)))
    return out
