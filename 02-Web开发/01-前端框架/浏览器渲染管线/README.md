# 浏览器渲染管线：样式 → 布局 → 绘制 → 合成

## 一、简介

MDN 把关键渲染路径概括为 *"Rendering steps include style, layout, paint, and in some cases compositing"*。这四个词几乎每个前端都背得出来，但真正影响性能的是三个细节：

1. **哪些改动会分别脏到哪一层**（改宽度要重排，改颜色只要重绘，改已提升层的 opacity 只要重新合成）；
2. **强制同步布局**：读几何属性会逼浏览器立刻算布局，"读写交错"因此比"先写后读"贵一个数量级；
3. **层提升**：`<video>` / `<canvas>` / `will-change` / 3D transform / 动画中的 opacity 会把节点连同后代提到独立层，之后的合成可以完全交给 GPU。

本 demo 把这套管线做成一个可计数的模拟器：改一次属性，`layouts / paints / composites` 三个计数器就告诉你代价落在哪。

代码：`render_pipeline.py` + `selfcheck_render_pipeline.py`（26 断言全通过）+ `render_pipeline.go`。

## 二、原理详解

### 2.1 四类改动，三条路径

| 改动 | 触发 | 依据 |
| --- | --- | --- |
| `width` / `height` / `margin` / `font-size` / `display` | layout → paint → composite | 几何变了，位置尺寸必须重算 |
| `color` / `background` / `box-shadow` | paint → composite | 不影响几何，只需重新栅格化 |
| 已提升层的 `opacity` / `transform` | composite | 层内容没变，只是合成参数变了 |
| 未提升元素的 `opacity` | paint → composite | 没有独立层可复用，得重画 |

MDN 的原话是 *"A reflow sparks a repaint and a re-composite"*——重排是最贵的，因为它把后面两阶段全部拖下水。

### 2.2 layout 与 reflow 是同一件事的两次

> *"The first time the size and position of each node is determined is called **layout**. Subsequent recalculations of layout are called **reflows**."*

经典触发场景是没写尺寸的 `<img>`：首帧布局时不知道图多大，只能留占位；图到了以后尺寸确定 → reflow → repaint → re-composite。MDN 明确说：*"Had we defined the dimensions of our image, no reflow would have been necessary"*。本 demo 用两条对偶断言把这差别锁住。

### 2.3 强制同步布局（layout thrashing）

浏览器本来会把布局推迟到下一帧统一做。但**读取几何属性**（`offsetWidth` / `getBoundingClientRect`）要求一个**此刻正确**的值，于是浏览器被迫立刻 flush 一次布局。

```
写 w 读 w 写 w 读 w 写 w 读 w 写 w 读 w  →  5 次布局
写 w 写 w 写 w 写 w 写 w 读 w              →  1 次布局
```

同样的 5 次写入、同样的最终状态，交错写法多付 4 次完整布局。这是前端性能优化里性价比最高的一条。

### 2.4 层与合成

- 自带层：`<video>`、`<canvas>`、`<iframe>`；
- 触发层：`will-change`、3D transform、动画中的 `opacity`；
- 归属规则：*"These nodes will be painted onto their own layer, along with their descendants, unless a descendant necessitates its own layer"* —— 后代默认跟着最近的层祖先，但自己需要层时独立。

一个元素**只有在独立层上**改 opacity/transform 才是 composite-only；否则照样要重绘。这一点常被误传成"改 opacity 一定不触发重绘"。

### 2.5 主线程预算

> *"everything occupying the main thread … must take the browser less than 16.67ms"*

60fps 下一帧只有 16.67ms，而 style + reflow + paint 全在主线程；合成可以在 GPU/合成线程上做——这是层提升真正的价值：把主线程的工作挪走。

## 三、环境

- Python 3.8+（标准库）
- Go 1.20+（对照）

## 四、运行方式

```bash
python selfcheck_render_pipeline.py
# render_pipeline: 26/26 assertions passed
```

## 五、注意事项与常见坑

1. **样式写入要先比对旧值。** 写相同值应判为 `noop`；否则"改 5 次"里若有一次与初值相同，布局次数会比预期少 1，断言就对不上（本 demo 开发期踩到）。
2. **`display:none` 的节点不在 render tree 里**，不参与布局、也不产生盒；它的后代同样被剪掉。
3. **composite-only 的判据是「有独立层」而不是「属性名」**。同一个 `opacity`，在 `<canvas>` 上只合成，在普通 `<p>` 上要重绘。
4. **`will-change` 不是免费的**：它把元素提升到独立层，代价是显存与层树管理开销；滥用会让合成本身变成瓶颈。
5. **帧耗时属于环境相关量**，本 demo 只打印不断言（`frame budget: 3.00 ms / 16.67 ms`），不要拿它做判定。

## 六、性能边界

- 布局是 O(render tree 节点数) 的遍历，且一旦 dirty 就是**全量**重排（真实浏览器有 dirty-bit 局部优化，本模型未建模）。
- 交错读写的代价是 O(写次数 × 树大小)，在深树上会迅速吃掉整帧预算。
- 层数量上升后，合成阶段的开销从"一次全屏合成"变成"多次层合成 + 混合"。

## 七、参考资料（实际读过）

- MDN · How browsers work（parsing / style / layout / paint / compositing、reflow 与 repaint 的因果关系、层提升条件、16.67ms 预算、TTI 50ms 定义）— https://developer.mozilla.org/en-US/docs/Web/Performance/How_browsers_work
