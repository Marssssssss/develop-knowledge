# -*- coding: utf-8 -*-
"""YOLO 检测头自检:锚框解码 / 网格量化 / 正负忽略分配 / 分类器口径 / k-means 锚框 / 损失。

所有结论都对应论文里的可引用表述,数值一律当场算出来并与解析值比对(浮点给容差)。
"""
import math

import yolo_head as Y

TOTAL = [0, 0]
FAILS = []


def check(name, cond, detail=""):
    TOTAL[0] += 1
    if cond:
        TOTAL[1] += 1
        print(f"  [PASS] {name}  {detail}")
    else:
        FAILS.append(name)
        print(f"  [FAIL] {name}  {detail}")


def lcg(seed=12345):
    state = [seed]

    def nxt():
        state[0] = (1103515245 * state[0] + 12345) % (2 ** 31)
        return state[0] / float(2 ** 31)

    return nxt


def synthetic_boxes(clusters=((40.0, 40.0), (120.0, 60.0), (200.0, 240.0)), per=100, jitter=0.12):
    """三个已知尺寸簇的合成框,用于检验 k-means 能否把它们还原出来。"""
    rnd = lcg()
    boxes = []
    for w0, h0 in clusters:
        for _ in range(per):
            boxes.append((w0 * (1.0 + jitter * (2.0 * rnd() - 1.0)),
                          h0 * (1.0 + jitter * (2.0 * rnd() - 1.0))))
    return boxes


def main():
    print("== A. 锚框解码:sigmoid 中心 + 指数尺寸 ==")
    check("σ(0) == 0.5", Y.sigmoid(0.0) == 0.5, "0.5")
    rng_ok = all(0.0 < Y.sigmoid(x) < 1.0 for x in (-36.0, -5.0, -1.0, 0.0, 1.0, 5.0, 36.0))
    check("σ 在 |x| <= 36 时严格落在 (0,1) -> 中心永远落在当前格内", rng_ok, "")
    check("坑:正向上饱和更早 —— σ(37) 在 float64 下就是精确的 1.0(中心可落到格边界)",
          Y.sigmoid(36.0) < 1.0 and Y.sigmoid(37.0) == 1.0,
          f"σ(36)={Y.sigmoid(36.0)!r} σ(37)={Y.sigmoid(37.0)!r}")
    check("负向不对称:σ(-37) 仍是可表示的 e^-37,要到指数下溢才归零",
          Y.sigmoid(-37.0) > 0.0 and Y.sigmoid(-1000.0) == 0.0,
          f"σ(-37)={Y.sigmoid(-37.0):.3e}")
    sym = max(abs(Y.sigmoid(-x) - (1.0 - Y.sigmoid(x))) for x in (0.3, 1.0, 4.0))
    check("σ(-x) == 1 - σ(x)", sym < 1e-15, f"max diff {sym:.2e}")
    box = Y.decode_box((0.0, 0.0, 0.0, 0.0), (116.0, 90.0), (5, 7), 32)
    check("t 全零时中心落在格的几何中心(5.5·32, 7.5·32)", box == (176.0, 240.0, 116.0, 90.0),
          f"{box}")
    b1 = Y.decode_box((0.0, 0.0, 1.0, 0.0), (116.0, 90.0), (0, 0), 32)
    check("tw = 1 -> 宽度放大 e 倍", abs(b1[2] - 116.0 * math.e) < 1e-12,
          f"{b1[2]:.6f} vs {116.0 * math.e:.6f}")
    rt = True
    for true_box in ((191.0, 245.0, 120.0, 88.0), (0.5 * 32, 0.5 * 32, 116.0, 90.0)):
        t, cell = Y.encode_box(true_box, (116.0, 90.0), 32)
        back = Y.decode_box(t, (116.0, 90.0), cell, 32)
        rt = rt and max(abs(back[i] - true_box[i]) for i in range(4)) < 1e-9
    check("encode -> decode 往返无损(误差 < 1e-9)", rt, "")
    inside = all((5 * 32) < Y.decode_box((tx, 0.0, 0.0, 0.0), (116.0, 90.0), (5, 0), 32)[0]
                 < (6 * 32) for tx in (-20.0, 0.0, 20.0))
    check("任意 tx 都无法把中心推出当前格(这是 sigmoid 归一化的目的)", inside, "")
    lt = max(abs(Y.logit(Y.sigmoid(x)) - x) for x in (-5.0, -1.0, 0.0, 1.0, 5.0))
    check("logit 是 sigmoid 的反函数(编码用)", lt < 1e-9, f"max diff {lt:.2e}")

    print("\n== B. 网格量化误差 ==")
    check("最大量化误差 = 半个 stride = 16 px", Y.max_quantization_error(32.0) == 16.0, "")
    check("三个尺度的量化误差 16 / 8 / 4 px",
          [Y.max_quantization_error(s) for s in Y.DEFAULT_STRIDES] == [16.0, 8.0, 4.0], "")
    cell = (int(191 // 32), int(245 // 32))
    got = Y.decode_box((0.0, 0.0, 0.0, 0.0), (116.0, 90.0), cell, 32)
    err = (abs(191 - got[0]), abs(245 - got[1]))
    check("真实中心 (191,245) 被量化到格 (5,7) 的中心,误差 (15,5) <= (16,16)",
          cell == (5, 7) and err == (15.0, 5.0), f"cell={cell} err={err}")
    check("三尺度共存时总预测数 = 3·(13² + 26² + 52²) = 10647", Y.total_predictions() == 10647,
          f"{Y.total_predictions()}")
    only13 = Y.total_predictions(grids=(13,))
    check("去掉两个细尺度后预测数只剩 507(少 21 倍)",
          only13 == 507 and abs(10647 / only13 - 21.0) < 1e-12, f"{only13}")
    shp = Y.head_tensor_shape(13)
    check("头部张量形状 13x13x3x85 = 43095",
          shp == (13, 13, 3, 85) and shp[0] * shp[1] * shp[2] * shp[3] == 43095, f"{shp}")
    check("格坐标按行优先枚举(与特征图内存顺序一致)",
          Y.grid_cells(2) == [(0, 0), (1, 0), (0, 1), (1, 1)], f"{Y.grid_cells(2)}")

    print("\n== C. 锚框分配:负责 / 忽略 / 负样本 ==")
    check("同尺寸形状 IoU == 1", Y.shape_iou((10.0, 10.0), (10.0, 10.0)) == 1.0, "")
    check("同心 (10,10) 与 (20,20) 的 IoU == 0.25(面积比 1:4)",
          Y.shape_iou((10.0, 10.0), (20.0, 20.0)) == 0.25, "0.25")
    check("同心 (10,10) 与 (10,20) 的 IoU == 0.5(边界值)",
          Y.shape_iou((10.0, 10.0), (10.0, 20.0)) == 0.5, "0.5")
    r = Y.assign_anchors((100.0, 100.0), [(100.0, 100.0), (80.0, 90.0), (50.0, 200.0)])
    check("IoU 最大的锚框负责该目标", r["responsible"] == 0 and r["ious"][0] == 1.0,
          f"ious={[round(x, 4) for x in r['ious']]}")
    check("非最优但 IoU 0.72 > 0.5 -> 进入忽略集",
          r["ignore"] == [1] and abs(r["ious"][1] - 0.72) < 1e-12, f"ignore={r['ignore']}")
    check("IoU 0.333 < 0.5 -> 作为负样本参与惩罚",
          r["negative"] == [2] and abs(r["ious"][2] - 1.0 / 3.0) < 1e-12, f"negative={r['negative']}")
    check("恰有 1 个负责锚框", len({r["responsible"]}) == 1, "")
    b = Y.assign_anchors((10.0, 10.0), [(10.0, 10.0), (10.0, 20.0)])
    check("IoU 恰为 0.5 不忽略(论文判据是 strictly > 0.5,须用 > 而非 >=)",
          b["ignore"] == [] and b["negative"] == [1], f"ignore={b['ignore']}")
    check("忽略集与负样本集互斥且并集为全部非负责锚框",
          set(r["ignore"]).isdisjoint(r["negative"])
          and sorted(r["ignore"] + r["negative"] + [r["responsible"]]) == [0, 1, 2], "")

    print("\n== D. 分类器口径:softmax vs 独立 logistic ==")
    sm = Y.softmax([8.0, 6.0])
    check("softmax([8,6]) 归一化为 1", abs(sum(sm) - 1.0) < 1e-15, f"sum={sum(sm):.16f}")
    check("softmax([8,6]) = (0.880797, 0.119203)",
          abs(sm[0] - 0.8807970779778823) < 1e-12 and abs(sm[1] - 0.1192029220221176) < 1e-12,
          f"{[round(x, 6) for x in sm]}")
    lg = [Y.sigmoid(8.0), Y.sigmoid(6.0)]
    check("同一组 logits 下两个独立 logistic 输出都 > 0.99(允许重叠标签同时为真)",
          lg[0] > 0.99 and lg[1] > 0.99, f"{[round(x, 6) for x in lg]}")
    check("softmax 把第二名压到 0.119 < 0.5(强制互斥,重叠标签无法表达)",
          sm[1] < 0.5, f"p2={sm[1]:.6f}")
    inv = Y.softmax([108.0, 106.0])
    check("softmax 平移不变(先减 max 保证数值稳定)",
          max(abs(inv[i] - sm[i]) for i in range(2)) < 1e-15, "")
    check("softmax([1000,1000]) 不溢出,得 (0.5, 0.5)",
          Y.softmax([1000.0, 1000.0]) == [0.5, 0.5], "")
    l_soft = Y.class_loss_softmax([8.0, 6.0], 1)
    l_logi = Y.class_loss_logistic([8.0, 6.0], [1.0, 1.0])
    check("同一 logits:softmax 交叉熵 2.13(第二名被判错)而 logistic BCE 仅 0.0028",
          abs(l_soft - 2.1269280110429727) < 1e-12 and l_logi < 0.01,
          f"soft={l_soft:.6f} logi={l_logi:.6f}")

    print("\n== E. k-means 锚框聚类(d = 1 - IoU) ==")
    check("相同尺寸的 k-means 距离为 0", Y.anchor_distance((50.0, 50.0), (50.0, 50.0)) == 0.0, "")
    check("尺寸差一倍时距离 = 1 - 0.25 = 0.75",
          abs(Y.anchor_distance((10.0, 10.0), (20.0, 20.0)) - 0.75) < 1e-15, "0.75")
    check("形状越接近距离越小(单调性,代替欧氏距离的动机)",
          Y.anchor_distance((100.0, 100.0), (110.0, 110.0))
          < Y.anchor_distance((100.0, 100.0), (200.0, 200.0)), "")
    boxes = synthetic_boxes()
    km1 = Y.kmeans_anchors(boxes, 1)
    km3 = Y.kmeans_anchors(boxes, 3)
    i1, i3 = Y.avg_iou(boxes, km1), Y.avg_iou(boxes, km3)
    truth = [(40.0, 40.0), (120.0, 60.0), (200.0, 240.0)]
    oracle = Y.avg_iou(boxes, truth)          # 用真尺寸当锚框时的上界
    check("k=3 达到 oracle 上界的 98% 以上(oracle = 直接用三个真尺寸当锚框)",
          i3 > 0.98 * oracle and i3 > i1 + 0.2,
          f"oracle={oracle:.4f} k=1={i1:.4f} k=3={i3:.4f}")
    rec = sorted(km3)
    tru = sorted(truth)
    check("还原出的锚框尺寸与真值接近(相对误差 < 10%)",
          all(abs(rec[j][k] - tru[j][k]) / tru[j][k] < 0.10 for j in range(3) for k in range(2)),
          f"{[(round(w, 1), round(h, 1)) for w, h in rec]}")
    ks = [1, 2, 3, 5, 9]
    curve = [Y.avg_iou(boxes, Y.kmeans_anchors(boxes, k)) for k in ks]
    check("avgIoU 随 k 单调不减(锚框越多越能覆盖真实尺寸)",
          all(curve[i] <= curve[i + 1] + 1e-9 for i in range(len(curve) - 1)),
          f"{[round(v, 4) for v in curve]}")
    hp = Y.hand_picked_anchors()
    ihp = Y.avg_iou(boxes, hp)
    i5 = Y.avg_iou(boxes, Y.kmeans_anchors(boxes, 5))
    check("同一批框上:k-means 5 个锚框优于 RPN 手工 9 个(论文结论在合成数据上的复现)",
          i5 > ihp and len(hp) == 9, f"kmeans k=5 {i5:.4f} vs hand-picked 9 {ihp:.4f}")

    print("\n== F. 损失与忽略规则的必要性 ==")
    check("BCE 在预测正确时为 0", Y.bce(1.0, 1.0) < 1e-11 and Y.bce(0.0, 0.0) < 1e-11, "")
    check("BCE(p=0.5, y=1) == ln2", abs(Y.bce(0.5, 1.0) - math.log(2.0)) < 1e-15,
          f"{Y.bce(0.5, 1.0):.16f}")
    preds, targets = [0.9, 0.0, 0.1], [1.0, 1.0, 0.0]
    with_ignore = Y.objectness_loss(preds, targets, ignored=[1])
    without = Y.objectness_loss(preds, targets)
    check("被忽略的锚框贡献 0,总损失只有另两个锚框的量级(0.2107)",
          abs(with_ignore - 0.21072103131565256) < 1e-12, f"{with_ignore:.6f}")
    check("若把'自信但错误的'锚框当负样本,单个样本就把损失放大 100 倍以上",
          without > 100 * with_ignore, f"{without:.4f} vs {with_ignore:.6f}")
    check("类别 BCE 逐类独立:抬高一个'目标为 0'的类的 logit,损失单调增加",
          Y.class_loss_logistic([8.0, -6.0], [1.0, 0.0])
          < Y.class_loss_logistic([8.0, 6.0], [1.0, 0.0]),
          f"{Y.class_loss_logistic([8.0, -6.0], [1.0, 0.0]):.6f} -> "
          f"{Y.class_loss_logistic([8.0, 6.0], [1.0, 0.0]):.6f}")

    print(f"\n结果:{TOTAL[1]}/{TOTAL[0]} 通过" + (f",失败:{FAILS}" if FAILS else ""))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
