# 游戏动画

## 类型

- **骨骼动画**：最常用，蒙皮权重驱动
- **顶点动画**：粒子/布料常用
- **Morph Target**：表情融合
- **程序化动画**：Inverse Kinematics（IK）

## 子主题

- [x] [IK 算法 — FABRIK](./IK算法/FABRIK/) — 位置空间启发式 IK（Aristidou & Lasenby 2011），3-10 轮收敛

## 已完成 demo

| ID | 目录 | 核心机制 |
| --- | --- | --- |
| 665 | [glTF 蒙皮与骨骼动画](./glTF蒙皮与骨骼动画/) | 线性混合蒙皮：`jointMatrix = globalJoint · inverseBind`、只应用关节变换（mesh 节点变换被忽略）、unorm 权重和 MUST 为 255/65535、±45° 各半混合收缩到 `cos45°` |
| 666 | [动画采样与四元数插值](./动画采样与四元数插值/) | Appendix C 四种取值公式：STEP / LINEAR / SLERP（最短路）/ CUBICSPLINE，切线单位是「每秒」 |
| 667 | [Godot 轨道插值与循环模式](./Godot轨道插值与循环模式/) | `Animation::_find` 二分与 `len = _find(keys, length)+1` 截断；LOOP_NONE / LINEAR / PINGPONG 的下标与端点记账 |
| 668 | [Godot 混合空间 1D 与 2D](./Godot混合空间1D与2D/) | 一维两侧线性插值、插值与离散的平局取向相反、sync 加权平均长度、2D 重心坐标与外部点投影回退 |
| 669 | [Godot 状态机与交叉淡入](./Godot状态机与交叉淡入/) | `fade_blend = MIN(1, fading_pos/fading_time)` 与 `CMP_EPSILON` 兜底、优先级取小且平局取后、travel 绕过条件与优先级 |

> 665 / 666 依据 KhronosGroup/glTF@main `Specification.adoc`；667 / 668 / 669 依据 godotengine/godot@master 源码。均为原文实读后转写，0 检索额度。

## 待研究

- [ ] CCD IK 算法（与 FABRIK 对比）
- [x] 蒙皮数学（Skinned Mesh，骨骼权重 + 矩阵混合）— 已由 665 覆盖
- [x] Animation State Machine（动画状态机）— 已由 669 覆盖
- [ ] Morph Target / BlendShape（glTF `mesh.primitives.targets` 与稀疏访问器）
- [ ] 双四元数蒙皮（DQS）与 LBS 收缩的修复对比
- [ ] 动画压缩：关键帧抽取（decimation）与定点量化
- [ ] Root Motion 与位移提取
- [ ] Motion Matching 与特征匹配代价
- [ ] Additive 动画与参考姿态（additive layer track）
