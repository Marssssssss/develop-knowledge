# UI 合批（Batch Build）与 Canvas 重建（Rebuild）

## 简介

UI 卡顿的绝大多数原因不在"draw call 太多"，而在**Canvas 反复重算批次**与**overdraw 吃满 fill-rate**。Unity 官方教程《Optimizing Unity UI》把 UGUI 的底层行为完整摊开讲了一遍，其中的模型（Canvas 缓存批次 / dirty 触发 rebatch / Sub-canvas 隔离 / PerformUpdate 三步）是自研 UI 与任何引擎 UI 的公共参照。

- **Canvas**：native 组件，把下辖 mesh 合并成 batch、生成渲染命令；结果**缓存**，直到变脏
- **rebatch / batch build**：重新计算批次的过程（按 depth 排序 + 检查重叠/共享材质，多线程）
- **rebuild**：C# 侧重算 Layout 与 Graphic 的 mesh，由 `CanvasUpdateRegistry.PerformUpdate` 驱动
- **Sub-canvas**：嵌套 Canvas，隔离父子脏标记
- **overdraw**：UI 几何全在 Transparent queue，**被完全遮挡的像素照样被采样**

官方给出的四类常见问题（本 demo 逐条给出可观测指标）：

| 类别 | 表现 | 可观测指标 |
| --- | --- | --- |
| fill-rate 过载 | fragment shader 打满 | 平均 overdraw 倍数 |
| 单次 rebatch 太贵 | 批数多、排序量大 | 一次 dirty 的重算元素数 |
| over-dirtying | 每帧都在重建 | 每帧脏元素数 |
| 顶点生成过慢（多为文本） | `Text_OnPopulateMesh` 热点 | 文本 mesh 顶点数 |

## 原理详解

### 1. rebatch：为什么"层级顺序"能决定 draw call

```
Canvas 下辖 mesh → 按 depth 排序 → 逐个检查是否与当前批共享 (material, texture)
   共享 → 追加到当前批
   不共享 → 开新批
```

于是**同样的元素集合，排列不同，draw call 可以差几十倍**（本 demo [11] 实测）：

| 排列 | draw call |
| --- | --- |
| 50 张图按两种图集交替（A B A B …） | **50** |
| 50 张图按图集归拢（A×25 后 B×25） | **2** |

最小的三元素例子（[3]）：`A1(atlas_a) - B(atlas_b) - A2(atlas_a)` → 3 批；改成 `A1 - A2 - B` → 2 批。

实际项目里最容易踩的三处：

- **Text 夹在 Image 中间**：Text 用独立字体材质/图集 → 必然断批（[4] 实测 3 批 → 归拢后 2 批）
- **Mask**：Mask/Mask2D 引入 stencil 材质实例，自身就与周围断批（[5]）
- **图集没打全**：两张图分属不同 atlas，即使看起来一样也会断批

### 2. rebuild：每帧一次的三步

```
WillRenderCanvases（每帧一次）
  └─ CanvasUpdateRegistry.PerformUpdate()
       ① 脏 Layout 重建   ← 按**层级深度**排序（parent 越少越先）
       ② Clipping 剔除    ← ClippingRegistry.Cull（Mask 相关）
       ③ 脏 Graphic 重建  ← **不排序**，按 IndexedSet 注册顺序
```

Layout 必须分层级先后：靠近根的布局可能改变嵌套布局的尺寸，所以要先算（本 demo [9] 实测顺序 `[根, 父, 子]`，而 Graphic 保持 `[graphic1, graphic2]` 的注册序）。Graphic 重建内部又分两步：顶点脏 → 重建 mesh；材质脏 → 更新 CanvasRenderer 的材质。

### 3. Sub-canvas 隔离：拆分 Canvas 的收益与代价

> "Sub-canvases isolate their children from their parent; a dirty child will not force a parent to rebuild its geometry, and vice versa."

实测（[10]）：100 个静态元素 + 1 个动态血条

| 结构 | 一次 dirty 的重算元素数 |
| --- | --- |
| 全部挂在一个 Canvas | **101** |
| 动态元素拆进 Sub-canvas | **1** |

代价：Sub-canvas 之间无法合批，draw call 会上升；官方也提醒不要"把 UI 拆成几十个 Sub-canvas"，移动端通常 2~3 个就够。

### 4. overdraw：为什么隐藏 UI 要"禁用"而不是"透明"

> "each pixel rasterized from a polygon will be sampled, even if it is wholly covered by other, opaque polygons"
> "make sure that no UI elements are hidden by setting their alpha to 0, as the element will still be sent to the GPU"

实测（[7]）：8 层全屏元素 → 平均 overdraw = 8.0；关掉被完全遮住的底板 → 立刻降回 7.0。若元素根本不需要绘制，官方建议**直接删掉 Graphic 组件**（raycast 仍然可用，见同大类"事件分发"demo 的命中测试）。

## 对比 / 选型

| 手段 | 降什么 | 代价 |
| --- | --- | --- |
| 打图集 | draw call | 内存上升、动态图不宜入集 |
| 调整层级使同材质相邻 | draw call | 可能改变视觉层次 |
| 拆 Sub-canvas | rebatch CPU | draw call 上升、无法跨 Canvas 合批 |
| 关掉全屏遮挡下的 UI | fill-rate | 需要显隐管理 |
| 删掉冗余 Graphic | fill-rate + 顶点生成 | 需要保证 raycast 需求（通常仍满足） |

## 环境准备

- Python ≥ 3.8（标准库）/ Go ≥ 1.21，无第三方依赖

## 运行方式

```bash
cd python && python3 ui_batching.py    # 23 项断言（算法在 ui_batching_core.py）
cd go     && go run ui_batching.go     # 打印各场景结果
```

## 关键代码片段

```python
def build_batches(elements):
    """rebatch：按 depth 排序后贪心成批（共享 material+texture）。"""
    ordered = sorted(elements, key=lambda e: e.depth)
    batches, cur = [], []
    for e in ordered:
        if not e.graphic:
            continue                       # 无 Graphic → 不产生绘制
        if batches and batches[-1][0].batch_key == e.batch_key:
            batches[-1].append(e)
        else:
            batches.append([e])            # 材质或纹理不同 → 开新批
    return batches
```

## 性能与边界

- rebatch 的排序是 O(n log n)，但**它是多线程的**：移动端 SoC 核心少，收益远小于桌面（官方原话：性能在不同 CPU 架构上差异很大，尤其是移动 SoC 与 4 核以上桌面 CPU 之间）。
- overdraw 与分辨率线性相关：1080p 全屏一层 = 2,073,600 px 采样；8 层就是 1,658 万次 fragment 采样/帧。
- Sub-canvas 不是越多越好：每个 Sub-canvas 至少贡献 1 次 draw call，且打断父子合批。官方建议移动端 2~3 个。
- Layout 组件越多，`IndexedSet_Sort` / `CanvasUpdateRegistry_SortLayoutList` 的耗时越明显；官方建议能用 RectTransform 锚点表达的就不要用 Layout Group。

## 注意事项与常见坑

1. **别用 alpha=0 隐藏 UI** —— 元素照样进批次、照样采样像素。禁用 GameObject 或删掉 Graphic。
2. **"draw call 多"不一定是瓶颈**：官方明确说"任何靠 draw call 压垮 GPU 的项目，更可能先被 fill-rate 卡住"。先跑 Frame Debugger 看 fragment 阶段。
3. **动态元素与静态元素混在一个 Canvas** 会导致整个 Canvas 每帧重建（over-dirtying），这是最常见也最容易修的性能问题。
4. **Text 的 Best Fit / 频繁改字符串**会触发顶点重建，滚动列表里的文本尤其明显。
5. **Mask 的代价是双向的**：既断批，又增加 stencil 的读写带宽。
6. **拆分 Canvas 前先确认收益**：拆了之后仍然每帧脏，那只是把一次大重算换成多次小重算。
7. **全屏不透明 UI 打开时，把被遮住的世界相机关掉** —— 渲染器并不知道 UI 会盖住整个 3D 场景（官方原话）。

## 参考资料（实际阅读过的权威来源）

- [Unity Learn — Optimizing Unity UI](https://learn.unity.com/tutorial/optimizing-unity-ui) — Unity Technologies 官方教程。本 demo 全部规则来源：Canvas/rebatch/dirty 定义、Sub-canvas 隔离、batch building「按 depth 排序 + 检查重叠与共享材质」、`CanvasUpdateRegistry.PerformUpdate` 三步与 Layout 按层级排序、UI 几何走 Transparent queue 与「被覆盖像素照样采样」、四类常见问题与对应 Profiler 热点函数（IndexedSet_Sort / CanvasUpdateRegistry_SortLayoutList / Text_OnPopulateMesh / Shadow_ModifyMesh）、alpha=0 元素仍提交 GPU、全屏 UI 下应关闭世界相机
