# glTF 蒙皮与骨骼动画（Linear Blend Skinning）

## 这是什么

glTF 2.0 里「骨骼动画」的本质是**顶点位置的加权线性混合**：每个顶点记录最多 4 个关节索引与对应权重，运行时把每个关节的变换矩阵按权重加起来，再作用到顶点上。本 demo 用 Python / Go 各实现一份可运行的最小模型，把规范里那些「MUST / MUST NOT」逐条做成断言。

## 权威来源（实际读过）

- KhronosGroup/glTF@main `specification/2.0/Specification.adoc`（172470 B，经 GitHub API 取原文后本地解析）
  - §Skins / §Joint Hierarchy / §Skinned Mesh Attributes 三节（约 7.2 KB）
  - §Animations 一节（约 10.6 KB）
  - Appendix C: Animation Sampler Interpolation Modes

## 核心机制

### 1. 关节矩阵与「先乘 inverseBind」

规范原句（§Skinned Mesh Attributes）：

> per-joint inverse bind matrices (when present) **MUST** be applied before the base node transforms.

即关节矩阵取

```
jointMatrix(j) = globalJointTransform(j) · inverseBindMatrix(j)
```

顶点结果

```
v' = Σ_k w_k · jointMatrix(k) · v      （权重和为 1）
```

在绑定姿态下 `globalJointTransform(j) == inverse(inverseBindMatrix(j))`，故所有 `jointMatrix` 都是单位阵、`v' == v`。这一条被做成断言：绑定姿态必须逐分量还原原顶点。

### 2. 挂着 mesh 的那个节点的变换被忽略

规范原句（§Joint Hierarchy）：

> Only the joint transforms are applied to the skinned mesh; the transform of the skinned mesh node **MUST** be ignored.

紧随其后的示例中 `joints = [1, 2]`，于是 `node_0` 的 translation 与 `node_1` 的 scale 生效，而 `node_3`（mesh 节点的父节点）的 translation 与 `node_4`（mesh 节点自身）的 rotation **被忽略**。demo 里把 `node_3/node_4` 的平移旋转改成任意值，断言蒙皮结果逐分量不变。

注意「被忽略」不等于「mesh 节点不参与世界变换」——规范说的是**不给蒙皮计算贡献**，因为顶点已经被送进关节所处的空间了。

### 3. 顶点属性与权重编码

| 项 | 规范约束 |
| --- | --- |
| 每集合关节数 | ≤ 4（`JOINTS_n` / `WEIGHTS_n` 均为 `VEC4`） |
| `JOINTS_n` 分量类型 | uint8 或 uint16 |
| `WEIGHTS_n` 分量类型 | float32 / unorm8 / unorm16 |
| 非负 | 「joint weights **MUST NOT** be negative」 |
| 唯一性 | 「Joints **MUST NOT** contain more than one non-zero weight for a given vertex」 |
| 空槽 | 未使用的关节值 **SHOULD** 置 0 |
| 浮点权重和 | **SHOULD** 尽量接近 1.0，官方校验阈值 `2e-7 × 非零权重个数` |
| unorm8 / unorm16 | 归一化前整数和 **MUST** 为 255 / 65535 |

最后一条最有工程价值：`0.25 × 4` 直接四舍五入得到 `[64,64,64,64]`，和为 **256**，违反规范。规范给的提示是「weight sum should be renormalized after quantization」——实现上用**最大余数法**分配舍入误差，得到 `[64,64,64,63]`。权重误差会被关节位置放大，所以必须重归一。

### 4. 权重不归一会发生什么

真实管线**不做齐次除法**。`Σ w_k · (M_k · v)` 的第 4 个分量就是 `Σ w_k`，若权重和为 0.5，顶点会朝原点收缩一半（demo 实测 `(2,0,0) → (1,0,0)`）。这是要求权重归一的原因，也顺带说明为什么实现里不能随手加透视除法——那会把这个现象抹掉。

### 5. LBS 的固有缺陷：混合收缩

两个关节分别绕 Z 转 ±45°、各占 0.5 权重，顶点 `(1,0,0)` 的正确结果应该是「转 0°」即 `(1,0,0)`，但 LBS 给出的是两个旋转结果的**平均**：

```
0.5·(0.7071, 0.7071, 0) + 0.5·(0.7071, -0.7071, 0) = (0.7071, 0, 0)
```

长度从 1 掉到 0.7071（**收缩 29.3%**）。这就是「糖果纸 / 关节塌缩」现象的数学来源，也是双四元数蒙皮（Dual Quaternion Skinning）要解决的问题。本 demo **只记录现象**，未实现 DQS。

## 目录结构

```
python/skinning.py           矩阵/节点/皮肤模型 + 权重量化（约 210 行）
python/selfcheck_skinning.py 40 条断言，全部实跑通过
python/main.py               演示入口
go/main.go                   Go 侧同协议实现（无本机工具链，人工审查 + 静态检查）
```

## 运行

```bash
cd python
python selfcheck_skinning.py   # 断言总数 40，失败 0
python main.py
```

## 自检覆盖

- 绑定姿态：`jointMatrix` 为单位阵、蒙皮还原原顶点、改 mesh 节点链不影响结果
- 摆姿势：均匀缩放与平移在 `T·S·R·S⁻¹·T⁻¹` 中共轭抵消，结果恰为纯旋转 `R`
- 层级：父子关节的绑定位置分别为 `(0,12,0)`，旋转是「绕关节自身绑定位置」的 `T(t)·R·T(-t)`
- LBS 收缩：±45° 各半 → 长度 `cos45° = 0.7071`
- 权重编码：unorm8 和 255（且朴素四舍五入为 256）、unorm16 和 65535、`2e-7 × n` 阈值判定
- 非法输入：重复非零关节 / 负权重 / 关节越界 / 超 4 个 / 集合数不等 / IBM 第四行非 `[0,0,0,1]` / IBM 含 NaN / IBM 数量不足

## 踩坑记录

1. **重复关节的判定只看非零权重**：`JOINTS = [0,1,1,1]` 配 `WEIGHTS = [0.25]×4` 是**非法**的（关节 1 有 3 个非零权重），而 `[0,1,0,1]` 配 `[0.5,0.5,0,0]` 合法。首版断言用前者验证「绑定姿态不变」，直接被自己的校验拦下。
2. **绑定姿态必须在摆姿势之前采集**：`Skin(joints)` 在不传 `inverseBindMatrices` 时以**当前**姿态为绑定姿态；若先给关节写好旋转再建 skin，关节矩阵恒为单位阵，LBS 收缩实验会全盘失效（首版 5 条断言因此全错）。
3. **不要做齐次除法**：蒙皮结果的第 4 分量等于权重和，除掉它就把「权重不归一 → 收缩」的现象抹平了。
4. **「关节原点不动」的断言要送进绑定位置**：`jointMatrix` 作用在 `(0,0,0)` 上并不代表关节原点——关节绑定在世界 `(0,12,0)`，应断言 `(0,12,0)` 映射到自身。
