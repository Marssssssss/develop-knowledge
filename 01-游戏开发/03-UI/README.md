# 游戏 UI

UI 系统包括布局、事件分发、文本渲染、本地化、动画。本目录按 UI 子能力组织：`布局` / `事件` / `文本` / `性能`。

## 主流 UI 系统

- **Unity UGUI / UI Toolkit**
- **Unreal UMG / Slate**
- **Cocos Creator**
- **自研 UI**（很多大厂如网易、米哈游都有自研）

## 已完成 demo

| demo | 主题 | 权威依据 |
| --- | --- | --- |
| [九宫格缩放/](./九宫格缩放/) | 9-slice / border-image：切片、四步绘制、stretch/repeat/round/space 四种平铺 | W3C CSS Backgrounds Level 3 §5 |
| [流式布局与弹性尺寸/](./流式布局与弹性尺寸/) | Flexbox 主轴算法：§9.3 分行 + §9.7 弹性长度解析（冻结循环、min/max 违约） | W3C CSS Flexbox Level 1 |
| [事件分发与冒泡穿透/](./事件分发与冒泡穿透/) | 捕获/冒泡两阶段、AT_TARGET、两个 stop、派发中增删监听、命中测试与穿透 | WHATWG DOM §2.9 |
| [UI合批与Canvas重建/](./UI合批与Canvas重建/) | rebatch 贪心成批、Sub-canvas 隔离、PerformUpdate 三步、overdraw 量化 | Unity《Optimizing Unity UI》 |
| [MSDF多通道距离场/](./MSDF多通道距离场/) | median-of-three、角点象限编码、伪距离、n=3 最小维度、重建误差实测 | Chlumský 2015 硕士论文 |
| [文本渲染/SDF/](./文本渲染/SDF/) | 单通道有符号距离场、双线性重建、smoothstep 抗锯齿 | Valve SIGGRAPH 2007 |

## 待研究

- [x] 网格布局（九宫格、Flow）— 见 [九宫格缩放/](./九宫格缩放/) 与 [流式布局与弹性尺寸/](./流式布局与弹性尺寸/)
- [x] 事件冒泡与穿透 — 见 [事件分发与冒泡穿透/](./事件分发与冒泡穿透/)
- [x] UI 性能优化（合批）— 见 [UI合批与Canvas重建/](./UI合批与Canvas重建/)；**Overdraw 仅做了量化口径，未单独立 demo**
- [x] 文本渲染（Signed Distance Field）— 见 [文本渲染/SDF/](./文本渲染/SDF/)；多通道见 [MSDF多通道距离场/](./MSDF多通道距离场/)
- [ ] Overdraw 专项：填充率上限、裁剪与 scissor、UI 层与 3D 层的 compositing
- [ ] UI 动画与缓动（tween、缓动函数、UI 动画与布局的相互作用）
- [ ] 本地化与字体回退（多语言换行、fallback 字体链、双向文字 BiDi）
- [ ] 富文本排版（行内图片、超链接、 emoji 与字形回退）
- [ ] 九宫格的引擎差异：Android NinePatch（.9.png 黑线标记与内容区）导入自研管线
- [ ] 虚拟滚动列表（对象池、可视区间裁剪、动态高度估算）

## 参考资料

- [CSS Backgrounds and Borders Module Level 3 §5 Border Images](https://www.w3.org/TR/css-backgrounds-3/#border-images)
- [CSS Flexible Box Layout Module Level 1](https://www.w3.org/TR/css-flexbox-1/)
- [DOM Standard §2.9 Dispatching events](https://dom.spec.whatwg.org/#dispatching-events)
- [Unity Learn — Optimizing Unity UI](https://learn.unity.com/tutorial/optimizing-unity-ui)
- Chlumský, V. *Shape Decomposition for Multi-channel Distance Fields*, CVUT 2015
