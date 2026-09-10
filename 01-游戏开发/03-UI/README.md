# 游戏 UI

UI 系统包括布局、事件分发、文本渲染、本地化、动画。

## 主流 UI 系统

- **Unity UGUI / UI Toolkit**
- **Unreal UMG / Slate**
- **Cocos Creator**
- **自研 UI**（很多大厂如网易、米哈游都有自研）

## 已完成 demo

- [x] SDF 文本渲染（有符号距离场，放大对比 + 阴影描边特效）— 见 [文本渲染/SDF/](./文本渲染/SDF/)

## 待研究

- [ ] 网格布局（九宫格、Flow）
- [ ] 事件冒泡与穿透
- [x] 文本渲染（Signed Distance Field）— 已完成，见 [文本渲染/SDF/](./文本渲染/SDF/)；后续可继续 MSDF、字形光栅化
- [ ] UI 性能优化（合批、Overdraw）