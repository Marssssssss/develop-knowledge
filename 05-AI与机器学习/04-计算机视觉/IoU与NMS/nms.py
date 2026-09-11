# -*- coding: utf-8 -*-
"""IoU 与非极大值抑制 NMS — 目标检测后处理的最小实现(纯标准库)。

依据 torchvision 官方文档(pytorch.org vision ops):
- NMS "iteratively removes lower scoring boxes which have an IoU
  greater than iou_threshold with another (higher scoring) box"
- 框格式 (x1, y1, x2, y2);返回按分数降序的保留索引
- batched_nms 用"按类别加坐标偏移"的技巧把逐类 NMS 合成一次全局 NMS

4 个 demo:
1. torchvision 文档标准例(5 框)→ 保留 [0, 3]
2. iou_threshold 敏感性(0.3 / 0.5 / 0.7)
3. 逐类 NMS:同框不同类不互斥 + offset 技巧验证
4. 退化情形:完全相同的框 / 内含框 / 零面积框
"""
import itertools


def box_area(b):
    """框面积。规范框要求 x2>=x1 且 y2>=y1,零面积返回 0。"""
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def iou(a, b):
    """交并比 = 交集面积 / 并集面积。

    并集用 area(a)+area(b)-inter,分母加 eps 防 0;两个零面积框
    交比为 0(无信息),而非除零崩溃。
    """
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    union = box_area(a) + box_area(b) - inter
    return inter / union if union > 0 else 0.0


def nms(boxes, scores, iou_threshold):
    """贪心 NMS:按分数降序,逐个保留并抑制重叠超阈值的低分框。

    与 torchvision.ops.nms 语义一致:返回保留索引(按分数降序)。
    """
    order = sorted(range(len(boxes)), key=lambda i: -scores[i])
    keep, suppressed = [], set()
    for pos, i in enumerate(order):
        if i in suppressed:
            continue
        keep.append(i)
        for j in order[pos + 1:]:
            if j in suppressed:
                continue
            if iou(boxes[i], boxes[j]) > iou_threshold:
                suppressed.add(j)
    return keep


def batched_nms_by_offset(boxes, scores, class_ids, iou_threshold):
    """torchvision batched_nms 的 offset 技巧(源码级复刻)。

    给不同类别的框加上"只依赖类别 id 的大偏移",使异类框在几何上
    永不相交 → 一次全局 NMS 等价于逐类 NMS。
    """
    if not boxes:
        return []
    max_coord = max(max(b[0], b[1], b[2], b[3]) for b in boxes)
    offsets = [cid * (max_coord + 1) for cid in class_ids]
    shifted = [(b[0] + o, b[1] + o, b[2] + o, b[3] + o)
               for b, o in zip(boxes, offsets)]
    return nms(shifted, scores, iou_threshold)


def fmt(keep, boxes, scores):
    return ["idx%d score=%.2f box=%s" % (i, scores[i], boxes[i]) for i in keep]


def demo1_torchvision_example():
    print("=== demo 1: torchvision 文档标准例 → 保留 [0, 3] ===")
    boxes = [(100, 100, 200, 200),   # 0: 左上目标主框
             (105, 105, 205, 205),   # 1: 与 0 重叠
             (150, 150, 250, 250),   # 2: 与 0/1 都重叠
             (300, 300, 400, 400),   # 3: 右下独立目标
             (302, 302, 402, 402)]   # 4: 与 3 重叠
    scores = [0.95, 0.87, 0.72, 0.93, 0.81]
    keep = nms(boxes, scores, 0.5)
    print("保留:", fmt(keep, boxes, scores))
    print("抑制:", [i for i in range(5) if i not in keep])
    assert keep == [0, 3], "torchvision 文档示例输出应为 [0, 3]"
    print("PASS: 两组重叠各留最高分,独立目标不受影响\n")


def demo2_threshold_sensitivity():
    print("=== demo 2: iou_threshold 敏感性 ===")
    boxes = [(10, 10, 110, 110),     # A: 100x100
             (10, 90, 110, 190)]     # B: 与 A 纵向重叠 20 → IoU = 2000/20000
    scores = [0.9, 0.8]
    overlap = iou(boxes[0], boxes[1])
    print("A/B IoU = %.3f(纵向重叠 20%%,iou = 交/并)" % overlap)
    for thr in (0.05, 0.15):
        keep = nms(boxes, scores, thr)
        kept_b = 1 in keep
        print("  thr=%.2f → 保留 %s(%s B)" % (thr, keep, "留" if kept_b else "抑制"))
        # IoU=0.1:thr=0.05 时 B 被抑制;thr=0.15 时 B 保留
        assert kept_b == (thr > overlap)
    print("PASS: 阈值是'重叠容忍度',松阈值保留近邻目标,紧阈值只留一个\n")


def demo3_per_class():
    print("=== demo 3: 逐类 NMS(同框不同类不互斥) ===")
    boxes = [(10, 10, 100, 100), (12, 12, 102, 102),  # 人 + 车 几乎同框
             (200, 200, 300, 300)]                    # 远处的人
    scores = [0.9, 0.85, 0.8]
    class_ids = [0, 1, 0]                             # 0=person, 1=car
    global_keep = nms(boxes, scores, 0.5)
    perclass = []
    for c in sorted(set(class_ids)):
        idxs = [i for i in range(3) if class_ids[i] == c]
        sub = nms([boxes[i] for i in idxs], [scores[i] for i in idxs], 0.5)
        perclass += [idxs[j] for j in sub]
    offset_keep = batched_nms_by_offset(boxes, scores, class_ids, 0.5)
    print("全局 NMS(错):", sorted(global_keep), "<- 车框被人框吃掉")
    print("逐类 NMS(对):", sorted(perclass))
    print("offset 技巧 :", sorted(offset_keep))
    assert sorted(perclass) == sorted(offset_keep), "offset 技巧应与逐类 NMS 等价"
    assert 1 in perclass and 1 not in global_keep
    print("PASS: 检测头每类独立出框,后处理必须逐类做 NMS\n")


def demo4_degenerate():
    print("=== demo 4: 退化情形 ===")
    a = (10, 10, 110, 110)
    print("IoU(相同框)      = %.3f" % iou(a, a))
    assert iou(a, a) == 1.0
    inner = (30, 30, 90, 90)
    print("IoU(内含框)      = %.3f(小框全含于大框)" % iou(a, inner))
    assert 0 < iou(a, inner) < 1.0
    degenerate = (50, 50, 50, 50)
    print("IoU(零面积框)    = %.3f(不除零)" % iou(a, degenerate))
    assert iou(a, degenerate) == 0.0
    # 相同分数的平票:torchvision 文档明示 CPU/GPU 结果不保证一致
    boxes = [a, (10, 10, 110, 110)]
    scores = [0.9, 0.9]
    keep = nms(boxes, scores, 0.5)
    print("平票相同框保留   :", keep, "(只留一个;具体留哪个实现可自定)")
    assert len(keep) == 1
    print("PASS: 完全重叠 IoU=1,零面积不除零,平票仍只留一框\n")


def main():
    demo1_torchvision_example()
    demo2_threshold_sensitivity()
    demo3_per_class()
    demo4_degenerate()
    print("all 4 demos PASS")


if __name__ == "__main__":
    main()
