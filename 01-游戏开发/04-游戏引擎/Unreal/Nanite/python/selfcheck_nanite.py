"""selfcheck_nanite.py — nanite.py 的自检（模型与自检分文件，单文件 ≤300 行）。"""

from __future__ import annotations

from nanite import *  # noqa: F401,F403

_ASSERTIONS = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _ASSERTIONS
    if not cond:
        raise AssertionError(f"{label} 失败: {detail}")
    _ASSERTIONS += 1
    print(f"ok {_ASSERTIONS:>2} {label}" + (f"  [{detail}]" if detail else ""))


def main() -> None:
    levels, leaves = build_dag(leaf_count=64)
    all_clusters = [c for lv in levels for c in lv]
    roots = levels[-1]

    # 1) 构建：128 三角形 + 每层减半 + 50% 简化
    check("leaf cluster 固定 128 三角形", all(c.tris == CLUSTER_TRIS for c in all_clusters))
    check("每上一层 cluster 数减半",
          [len(lv) for lv in levels] == [64, 32, 16, 8, 4, 2, 1],
          f"{[len(lv) for lv in levels]}")
    check("组内共享 unioned error（同组误差完全相同）",
          all(len({c.error for c in levels[0][i:i + GROUP_SIZE]}) == 1
              for i in range(0, len(levels[0]), GROUP_SIZE)))
    check("组内共享 unioned bounds（同组包围球完全相同）",
          all(len({(c.center, c.radius) for c in levels[0][i:i + GROUP_SIZE]}) == 1
              for i in range(0, len(levels[0]), GROUP_SIZE)))

    # 2) 是 DAG 不是树：一个 parent 的孩子来自整组（> 二分叉）
    check("parent 的孩子数 = 组大小（DAG 而非二叉树）",
          len(levels[1][0].children) == GROUP_SIZE, f"{len(levels[1][0].children)}")
    check("组不满时按实际成员数（顶层组只剩 2 个）",
          len(roots[0].children) == 2, f"{len(roots[0].children)}")
    check("leaf 的 parent 不止一个（兄弟共享多个 parent）",
          len(leaves[0].parents) >= 2, f"{len(leaves[0].parents)}")

    # 3) 误差单调：parent >= child（构建期强制，否则 cut 不唯一）
    check("误差沿 parent→child 单调不增",
          all(p.error >= ch.error for c in all_clusters for p in c.parents for ch in [c]))
    raised = [c for lv in levels[1:] for c in lv if c.error > raw_error(c.level, c.cid, 0)]
    check("确有 parent 的误差是被强制抬高的（否则不必要）", len(raised) > 0, f"{len(raised)} 个")
    check("leaf（LOD0）误差为 0（凑近了就能画原始三角形）",
          all(c.error == 0.0 for c in leaves))

    # 4) LOD 选择：一个 cut —— 每条 root→leaf 路径恰好选中一个 cluster
    for thr in (0.5, 1.0, 2.0, 4.0):
        sel = set(id(c) for c in select(all_clusters, threshold=thr))
        bad = [p for p in paths_to_leaves(roots)
               if sum(1 for c in p if id(c) in sel) != 1]
        check(f"阈值 {thr} 下全部 {len(paths_to_leaves(roots))} 条路径各选中且仅选中 1 个 cluster",
              not bad, f"异常路径 {len(bad)} 条")
    sel1 = select(all_clusters, threshold=1.0)
    check("选中的 cluster 屏幕误差都 <= 阈值（亚像素 ⇒ 无可见跳变）",
          all(view_error(c) <= PIXEL_THRESHOLD + 1e-12 for c in sel1), f"{len(sel1)} 个")
    check("选中规则等价于 (parent 太粗 && 自己够细)",
          all(parent_view_error(c) > PIXEL_THRESHOLD and view_error(c) <= PIXEL_THRESHOLD
              for c in sel1))

    # 5) 阈值越大越粗（选中数不增），相机越远越粗
    counts = [len(select(all_clusters, threshold=t)) for t in (0.25, 1.0, 4.0, 16.0)]
    check("阈值放大 ⇒ 选中的 cluster 数单调不增", counts == sorted(counts, reverse=True),
          f"{counts}")
    near = len(select(all_clusters, threshold=1.0, camera=(0.0, 0.0, 6.0)))
    far = len(select(all_clusters, threshold=1.0, camera=(0.0, 0.0, 40.0)))
    check("相机拉远 ⇒ 选中的 cluster 数减少", far < near, f"near={near} far={far}")

    # 6) ParentError 剪枝：结果一致但评估更少
    sel_flat = select(all_clusters, threshold=1.0)
    sel_tree, evaluated = select_with_cull(roots, threshold=1.0)
    check("剪枝遍历与全量扫描结果一致",
          sorted(id(c) for c in sel_flat) == sorted(id(c) for c in sel_tree),
          f"{len(sel_flat)} vs {len(sel_tree)}")
    check("剪枝减少了评估次数", evaluated < len(all_clusters),
          f"{evaluated} < {len(all_clusters)}")

    # 7) visibility buffer：材质按像素求值一次，不随 overdraw 放大
    pixels = 1920 * 1080
    overdraw = [1] * (pixels // 2) + [4] * (pixels // 2)      # 一半像素被覆盖 4 次
    check("visibility buffer 的材质求值次数 = 像素数",
          shade_cost(pixels, overdraw, "visibility_buffer") == pixels)
    check("forward 的材质求值次数含 overdraw（更多）",
          shade_cost(pixels, overdraw, "forward") > pixels,
          f"{shade_cost(pixels, overdraw, 'forward')} > {pixels}")

    # 8) cluster 级剔除
    kept, occluded = frustum_cull(all_clusters)
    check("剔除后候选数下降", len(kept) < len(all_clusters),
          f"{len(kept)} / {len(all_clusters)}")
    check("确有被 HZB 判为遮挡的 cluster", occluded > 0, f"{occluded}")

    # 9) streaming：任何 cut 都能当叶，缺的子节点按需请求，久未绘制则驱逐
    st = Streamer()
    st.load(levels[0])
    cut = select(all_clusters, threshold=1.0)
    st.load(cut)
    check("刚加载完时 cut 全部驻留", not st.missing(cut), f"缺 {len(st.missing(cut))}")
    evicted = st.tick(drawn=cut[:2], keep_frames=0)
    check("久未绘制的 cluster 会被驱逐", len(evicted) > 0, f"{len(evicted)}")
    check("被驱逐后请求列表非空（按需回灌）", len(st.missing(cut)) > 0,
          f"{len(st.missing(cut))}")

    print(f"\n全部 {_ASSERTIONS} 条断言通过")


if __name__ == "__main__":
    main()
