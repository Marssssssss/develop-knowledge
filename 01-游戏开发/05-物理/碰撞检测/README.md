# 碰撞检测

判断两个物体是否相交，以及交点位置。

## 两阶段检测

- **Broad Phase**：粗筛，使用包围盒（AABB / OBB / Sphere）快速排除不可能相交的对
- **Narrow Phase**：精算，使用 SAT / GJK / 三角形相交

## 空间分割

- **四叉树 / 八叉树**（静态场景）
- **BVH**（动态场景）
- **Uniform Grid**（粒子）

## 已完成 demo

| Demo | 知识点 | 语言 |
| --- | --- | --- |
| [GJK/](./GJK/) | GJK 碰撞检测算法（Minkowski 差 + support 函数 + simplex 演化） | C / Python / Go |
| [SAT与EPA/](./SAT与EPA/) | 分离轴定理（SAT）+ EPA 求穿透深度/MTV；含包含修正与三重积退化 | Python |
| [BroadPhase与空间分割/](./BroadPhase与空间分割/) | 均匀网格 / 空间哈希 / 动态 AABB 树（Box2D v3）/ SAH-BVH（pbrt）四路交叉验证 | Python |

## 待研究

- [x] 四叉树动态构建 / BVH 加速（宽相）→ 已完成 [BroadPhase与空间分割/](./BroadPhase与空间分割/)（2026-09-21）
- [x] SAT（分离轴定理）→ 已完成 [SAT与EPA/](./SAT与EPA/)（2026-09-21）
- [x] EPA 求穿透深度（GJK 的标准配套）→ 已完成 [SAT与EPA/](./SAT与EPA/)（2026-09-21）
- [ ] 连续碰撞检测（TOI / CCD）与隧道效应
- [ ] 三角形-三角形精确相交（Möller / Ericson）
- [ ] OBB / 凸包 SAT 的 3D 推广（15 条候选轴）