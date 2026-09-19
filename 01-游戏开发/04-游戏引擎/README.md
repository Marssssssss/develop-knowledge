# 游戏引擎

主流商业/开源引擎对比，以及如何挑选/学习。

## 引擎对比

| 引擎 | 厂商 | 语言 | 适用 |
| --- | --- | --- | --- |
| Unity | Unity Technologies | C# | 移动、独立、跨平台 |
| Unreal | Epic Games | C++ / Blueprint | 3A、影视 |
| Godot | 开源 | GDScript / C# | 独立、轻量 |
| Cocos | 开源 | TS / C++ | 2D 移动 |
| 自研 | 各大厂 | — | 性能敏感项目 |

## 子领域

- [Unity/](./Unity/)
- [Unreal/](./Unreal/)
- [Godot/](./Godot/)
- [02-Archetype/](./02-Archetype/)
- [03-固定时间步/](./03-固定时间步/)

## 已完成 demo

| # | demo | 核心机制 | 语言 |
| --- | --- | --- | --- |
| 1 | [ECS](./ECS/) | sparse set 存储 + swap-remove + 最小集合查询 | C / Python / Go |
| 437 | [02-Archetype](./02-Archetype/) | 16KiB chunk、每组件一个数组、chunk 内 swap-remove、增删组件＝跨 archetype 搬移全部组件 | Python(27 断言) / Go |
| 438 | [03-固定时间步](./03-固定时间步/) | accumulator + 固定 dt + alpha 插值、0.25 秒钳位、死亡螺旋与限流 | Python(22 断言) / Go |
| 439 | [Unity/JobSystem与Burst](./Unity/JobSystem与Burst/) | 安全系统在调度时判冲突、JobHandle 依赖、ParallelFor 分批与「一次偷一半」、三种分配器寿命、HPC# 类型子集 | Python(29 断言) / Go |
| 440 | [Unreal/Nanite](./Unreal/Nanite/) | 128 三角形 cluster、group→simplify 50%→split、DAG、组内共享 unioned error、`ParentError>t && err<=t` 并行 cut、ParentError 剪枝、visibility buffer | Python(27 断言) / Go |
| 441 | [Godot/节点生命周期](./Godot/节点生命周期/) | `_enter_tree` 父先于子 / `_ready` 子先于父、queue_free 延迟到帧末、process 随帧率 vs physics 固定 60 | Python(28 断言) / Go |

## 待研究

- [x] Unity DOTS 官方包细节（Job System / Burst 与 ECS 的协作）— 2026-09-20 见 `Unity/JobSystem与Burst/`
- [ ] Unreal Lumen 原理（Nanite 已开线，见 `Unreal/Nanite/`）
- [ ] Godot 4 新特性（GDScript 2.0）— 节点/场景部分已开线，见 `Godot/节点生命周期/`
- [x] archetype 存储 demo（与 sparse set 对比实现）— 见 `02-Archetype/`
- [ ] 引擎资源管线（Addressables / 资产烘焙与热重载）
- [ ] Unreal 反射与 UObject GC
- [ ] 自研引擎的渲染图（Render Graph / Frame Graph）