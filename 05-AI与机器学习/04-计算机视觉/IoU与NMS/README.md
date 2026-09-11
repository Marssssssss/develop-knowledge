# IoU 与非极大值抑制 NMS(目标检测后处理)

## 简介

- 检测器(YOLO / Faster R-CNN / RetinaNet)对同一物体会输出**多个位置相近、分数不同的框**;NMS 是所有检测管线 visualization 前的标准后处理:**保留每组重叠框中分数最高者,抑制其余**。
- 判定"重叠"的度量是 **IoU(Intersection over Union,交并比)**= 交集面积 / 并集面积,取值 [0,1],1 表示完全重合。
- 关键概念:
  - **框格式** `(x1, y1, x2, y2)`:左上 + 右下角点(torchvision 约定)
  - **IoU 阈值**:典型 0.5;高于阈值视为"同一目标的重复检测"
  - **贪心策略**:按分数降序逐个保留,每次保留后抑制所有与它 IoU>阈值的剩余框
  - **逐类 NMS**:NMS 只在**同一类别内**进行,异类框不互斥
- 历史:NMS 是检测的古老组件;深度学习时代由 Faster R-CNN / YOLO 等固化进管线,变体有 Soft-NMS(降权替代删除)、DIoU-NMS(引入中心距)。

## 原理详解

```
输入: boxes N 个 + scores N 个 + iou_threshold
        │
        ▼ 按分数降序排序
   ┌─ 取当前最高分框 i → keep
   │        │
   │        ▼ 对剩余每个框 j:
   │   IoU(box_i, box_j) > threshold ? → 从候选中删除 j
   │        │
   └── 重复直到候选耗尽
        │
        ▼ 输出: keep(索引,按分数降序)

IoU 计算:
  inter_w = max(0, min(ax2,bx2) - max(ax1,bx1))   # 交集宽(不重叠为 0)
  inter_h = max(0, min(ay2,by2) - max(ay1,by1))
  inter   = inter_w * inter_h
  union   = area(a) + area(b) - inter              # 容斥原理
  IoU     = inter / union
```

torchvision 官方语义(文档原文):*"NMS iteratively removes lower scoring boxes which have an IoU greater than iou_threshold with another (higher scoring) box."* 注意是**严格大于**。

核心 API:

```
torchvision.ops.nms(boxes, scores, iou_threshold) -> keep
  boxes    (N,4), (x1,y1,x2,y2),要求 0 <= x1 < x2, 0 <= y1 < y2
  scores   (N,)
  返回     int64 索引,按分数降序

torchvision.ops.batched_nms(boxes, scores, idxs, iou_threshold)
  idxs 为类别 id;不同类别之间不做 NMS。
  源码技巧:给每类框加偏移 offset = idx * (max_coord + 1),
  异类框在几何上永不相交 → 一次全局 NMS 等价逐类 NMS。
```

## 对比 / 选型

| 方法 | 抑制方式 | 特点 |
| --- | --- | --- |
| 贪心 NMS | 硬删除 | 标准做法;密集目标(相邻两人)易误删 |
| Soft-NMS | 分数衰减 | 重叠框降权不删除,召回更高,多一次阈值 |
| DIoU-NMS | 距离感知 | IoU 相同时中心更近者优先抑制 |
| cluster/阈值替代 | — | YOLO 系可用 obj 阈值 + top-k 减少 NMS 输入量 |

## 环境准备

- 操作系统:任意(纯标准库)
- 语言版本:Python 3.8+
- 依赖:无

## 运行方式

```bash
python3 nms.py
```

## 关键代码片段

```python
def iou(a, b):
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])       # 交集左上 = max
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])       # 交集右下 = min
    inter = max(0, ix2-ix1) * max(0, iy2-iy1)         # 不相交则宽/高取 0
    union = area(a) + area(b) - inter                 # 容斥:多减了一次交
    return inter / union if union > 0 else 0.0

def nms(boxes, scores, thr):
    order = sorted(range(len(boxes)), key=lambda i: -scores[i])
    keep, dead = [], set()
    for i in order:
        if i in dead: continue
        keep.append(i)                                # 保留当前最高分
        for j in order:                               # 抑制重叠的低分框
            if j not in dead and iou(boxes[i], boxes[j]) > thr:
                dead.add(j)
    return keep
```

## 性能与边界

- 朴素实现 O(N²) IoU 计算;N 通常是 top-k 后的几百~几千,YOLOv5 默认 max_det=1000。
- torchvision 的 CUDA 版本按分数分桶做到近似 O(N);CPU 参考实现同为贪心。
- IoU 对**尺度敏感**:大框间 20% 重叠的绝对面积可能大于小框本身,小目标更易被抑制(小目标检测难点之一,GIoU/DIoU 损失的动机)。

## 注意事项与常见坑

1. **逐类 NMS 不能省**(demo 3):人框和车框重叠 0.9 也都该留;直接全局 NMS 会把低分类别吃掉。工程上用 batched_nms(按类偏移)或对每类分别调用。
2. **交并比公式记得减 inter**:union = area_a + area_b − inter,容斥原理;不减会低估 IoU、多删框。
3. **零面积 / 退化框**:除零要防(union>0 判断);x1>x2 的乱序框 max(0,·) 兜底,面积按 0 处理。
4. **平票不保证稳定**:torchvision 文档明示,分数完全相同的框在 CPU/GPU 上保留哪个**不保证一致**(类似 argsort 对重复值的行为);依赖保序的测试要加 tie-break。
5. **阈值方向别搞反**:iou_threshold 越小删得越狠(0.1 几乎只留一簇一个),越大越宽松;YOLO 默认 0.45~0.5,密集场景常调到 0.6。

## 参考资料(实际阅读过的权威来源)

- [torchvision.ops — PyTorch 官方文档](https://docs.pytorch.org/vision/0.9/ops.html) — nms/batched_nms 完整签名与语义原文、box_iou 等系列算子
- [torchvision.ops.boxes 源码(Detectron2 文档镜像)](https://detectron2.readthedocs.io/en/v0.6/_modules/torchvision/ops/boxes.html) — batched_nms 的 offset 技巧源码、平票 CPU/GPU 不一致说明
- [torchvision.ops 早期文档(v1.3)](https://pytorch.org/docs/1.3.0/torchvision/ops) — nms "iteratively removes lower scoring boxes" 的原始定义
