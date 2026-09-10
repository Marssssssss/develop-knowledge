# 碰撞检测

判断两个物体是否相交，以及交点位置。## 两阶段检测

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

## 待研究

- [ ] 四叉树动态构建
- [ ] SAT（分离轴定理）
- [ ] EPA 求穿透深度（GJK 的标准配套）