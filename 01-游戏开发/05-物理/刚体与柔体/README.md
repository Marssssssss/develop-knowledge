# 刚体与柔体

## 刚体

- 牛顿-欧拉方程
- 冲量法（Impulse-based）
- 顺序脉冲（Sequential Impulse）— Bullet / Box2D 主流
- 速度/位置约束 Baumgarte Stabilization

## 柔体

- 质点弹簧（Mass-Spring）
- PBD（Position Based Dynamics）
- XPBD（Extended PBD）
- SPH 流体

## 已完成 demo

| Demo | 知识点 | 语言 |
| --- | --- | --- |
| [PBD与XPBD约束求解/](./PBD与XPBD约束求解/) | PBD 式 10/11 与 XPBD 式 17/18 对拍；刚度依赖时间步的消解、GS vs Jacobi、全局阻尼 | Python |
| [布料模拟/](./布料模拟/) | 三角网格布的拉伸 + 两套弯曲（对角距离 vs 二面角）、子步循环、地面投影 | Python |
| [接触与冲量求解/](./接触与冲量求解/) | Box2D v3 `b2MakeSoft` 软约束、推测性接触、冲量 clamp、摩擦锥、恢复系数 | Python |

## 待研究

- [x] XPBD 软体约束 → 已完成 [PBD与XPBD约束求解/](./PBD与XPBD约束求解/)（2026-09-21）
- [x] Verlet / PBD 布料模拟 → 已完成 [布料模拟/](./布料模拟/)（2026-09-21）
- [ ] 弹簧阻尼系统最小 demo
- [ ] 刚体堆叠稳定性（warm start / 接触点回收）
- [ ] 绳索与头发动力学（Cosserat rod）