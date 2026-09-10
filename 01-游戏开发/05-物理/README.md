# 游戏物理

物理引擎负责刚体动力学、碰撞检测、布料、流体等。

## 主流物理引擎

- **PhysX**（NVIDIA，Unreal 默认）
- **Bullet**（开源）
- **Havok**（商业，多用于主机）
- **Box2D / Chipmunk2D**（2D）
- **Unity 自带 PhysX / DOTS Physics**

## 子领域

- [碰撞检测/](./碰撞检测/) — Broad Phase / Narrow Phase / 空间分割
- [刚体与柔体/](./刚体与柔体/) — 约束求解、PBD/XPBD

## 待研究

- [x] GJK 碰撞检测算法 → 已完成 [碰撞检测/GJK/](./碰撞检测/GJK/)（2026-09-11）
- [ ] 四叉树 / BVH 加速
- [ ] Verlet / PBD 布料模拟