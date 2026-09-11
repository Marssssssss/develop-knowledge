# IK 算法（Inverse Kinematics）

骨骼动画中的"末端驱动"问题：给定末端执行器的目标位置，让骨骼链自动计算各关节角度/位置。

## 子主题

- [FABRIK（Forward And Backward Reaching IK）](./FABRIK/) — Aristidou & Lasenby 2011 提出，**位置空间直接求解**，3-10 轮收敛，已被 Unreal / Unity3D / ROBLOX 等引擎集成

## 待研究

- [ ] CCD（Cyclic Coordinate Descent）— 与 FABRIK 对比最广的旋转 IK 启发式
- [ ] Skinned Mesh 蒙皮数学（骨骼权重 + 矩阵混合）
- [ ] Animation State Machine（动画状态机）
