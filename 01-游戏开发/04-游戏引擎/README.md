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

## 已完成 demo

- [x] ECS（Entity Component System）— 见 [ECS/](./ECS/)：sparse set 存储 + swap-remove + 最小集合查询，C / Python / Go

## 待研究

- [ ] Unity DOTS 官方包细节（Job System / Burst 与 ECS 的协作）
- [ ] Unreal Nanite / Lumen 原理
- [ ] Godot 4 新特性（GDScript 2.0）
- [ ] archetype 存储 demo（与 sparse set 对比实现）