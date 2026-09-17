# Tile-Based GPU 渲染架构(TBDR)

## 简介

移动 GPU(Mali / Adreno / PowerVR / Apple)与桌面独显的根本差异是**内存系统**:桌面 GPU 用 GDDR 显存,移动 SoC 的共享内存带宽只有 4-8 GB/s,而每字节约 150 pJ 的能耗意味着**光内存流量就能吃掉 0.6-1.2 W 功耗预算**。图块化渲染(tile-based rendering)为此而生:把 framebuffer 切成 8×8~16×16 的小块,所有颜色/深度/模板读写都在片上 tile memory 完成,每个 tile 结束时才 resolve 写回外部内存一次。

- **Binning**:提交三角形时不画,先算它覆盖哪些 tile,进各 tile 的 to-do list
- **Tile Memory**:片上缓存,整个 tile 的光栅化都在其中完成
- **Resolve**:tile 结束时把多采样平均成每像素一色,写回外部 framebuffer
- **loadOp / storeOp**:Vulkan/Metal 显式声明 tile memory 的初始化与写回
- **"免费"的 MSAA**:4 个采样只存在片上,resolve 时才平均,外部带宽不增

Arm 在所有 Mali GPU 上使用图块化;Qualcomm Adreno 与 Imagination PowerVR 是同思路的变体(后者的 TBDR 还做逐 tile 隐藏面消除)。

## 原理详解

### IMR vs TBR 的执行模型

```text
桌面 IMR(立即模式):               移动 TBR(图块化):
for triangle in triangles:          for tile in tiles:            # 逐 tile
  for fragment in triangle:           todo = load(to_do_list)      # binning 产物
    rasterize(读写系统内存)           load(tile memory, loadOp)     # 可省
                                      for triangle in todo[tile]:  # 只遍历本 tile 三角形
                                        rasterize(读写片上 tile memory)
                                      resolve → store(外部内存, storeOp)
```

三角形覆盖大半个 framebuffer 时,IMR 要写大片显存;TBR 把写局部化进 tile,且深度/模板的反复读写**根本不出芯片**。

### loadOp / storeOp:外部流量的两个阀门

| 操作 | 语义 | 带宽效果 |
| --- | --- | --- |
| loadOp = LOAD | tile memory 初始化要读回旧 framebuffer | 整帧回读,几乎翻倍带宽 |
| loadOp = CLEAR / DONT_CARE | 初始化不需要旧数据 | **零回读**(clear 在 tile memory 里做,是免费的) |
| storeOp = STORE | pass 结束写回该附件 | 必要时才付出 |
| storeOp = DONT_CARE | 不需要保留 | depth/stencil 这类"用完即弃"附件省一整张写回 |

Metal 的对应物:`MTLStorageModeMemoryless` 纹理(不分配系统内存);Vulkan:`TRANSIENT_ATTACHMENT` + `LAZILY_ALLOCATED`(Vulkan 允许 tile memory 不足时溢出到显存,Metal 不允许)。

### MSAA 4x 为什么"免费"

桌面 IMR:4x MSAA 的颜色/深度附件都是 4 倍大,外部流量 ×4。TBDR:4 个采样只在 tile memory 里存在,resolve(平均)发生在写回之前,外部只写每像素 1 个样本。代价转移到**片上**:tile memory 需求 ×4——这是 tile 尺寸做不大的原因之一。

### 单 pass 延迟渲染(Apple 特化)

Apple GPU 的 TBDR 允许 fragment shader **读取仍挂在 render pass 上的 render target**(programmable blending):G-buffer 生产与光照消费可以在**同一个 render pass** 内完成,G-buffer 纹理用 memoryless 存储,系统内存里完全不存在——桌面 IMR 必须两个 pass + 全量存取。

## 对比 / 选型

| 维度 | 桌面 IMR | 移动 TBR/TBDR |
| --- | --- | --- |
| overdraw 的外部带宽 | 线性放大(8 层 overdraw 下本 demo:8.45MB) | 不变(~0.39MB,21 倍差距) |
| MSAA 4x 外部带宽 | ×4 | ≈ 不变 |
| 帧内多 render pass | 便宜(barrier) | 每 pass 切换都可能触发 load/store |
| render pass 合并 | 无所谓 | **关键优化**:能合就合 |

## 环境准备

- Python 3.8+ / Go 1.18+,无第三方依赖(带宽记账模拟)

## 运行方式

```bash
python3 python/main.py
go run go/main.go
```

## 关键代码片段

```python
def tbr_bandwidth(tris, fb, depth_bps=4, load_clear=True, store_depth=True):
    todo = bin_triangles(tris, fb.w, fb.h, TILE)     # binning → to-do list
    bin_bytes = sum(len(v) * 64 for v in todo.values())
    load_bytes = 0
    if not load_clear:                                # loadOp=LOAD 才回读
        load_bytes += fb.w * fb.h * fb.bps
    store_bytes = fb.w * fb.h * fb.bps                # 颜色 resolve 后写回
    if store_depth:                                   # depth 用完即弃则可省
        store_bytes += fb.w * fb.h * depth_bps
    return bin_bytes, load_bytes, store_bytes
```

## 性能与边界

- 记账口径(256×256、RGBA8+D32、8 层 overdraw):IMR 外部 8.45MB vs TBR 0.39MB(**21.4 倍**)——比例随 overdraw 与分辨率放大
- to-do list 本身走系统内存(本 demo 按 64B/三角形记账),超多三角形时 binning 阶段本身成为成本
- tile memory 容量有限(决定可同时挂载的附件与 MSAA 倍数),超出就要溢出或拆 pass

## 注意事项与常见坑

- **别在 render pass 中途 vkCmdClearAttachments**:它不是免费的;clear 应该用 loadOp=CLEAR/DONT_CARE 在 tile memory 里做
- **别用 shader 手写常量色清屏**,同理
- **帧末把低分辨率游戏画面 vkCmdBlitImage 放大 + UI 用 loadOp=LOAD 叠加**是典型的多余往返(Arm 明确列为反模式)
- **depth 附件通常 storeOp=DONT_CARE**:每帧"用完即弃",写回纯属浪费
- **多 pass 前想清楚**:每个 pass 边界都可能把附件从 tile memory 倒出去再读回来;能合并的 pass 合并,能 memoryless 的附件别给系统内存

## 参考资料(实际阅读过的权威来源)

- [Arm Developer — How low can you go? Building low-power, low-bandwidth ARM Mali GPUs](https://developer.arm.com/community/arm-community-blogs/b/mobile-graphics-and-gaming-blog/posts/how-low-can-you-go-building-low-power-low-bandwidth-arm-mali-gpus) — 4-8 GB/s × 150 pJ/byte ≈ 0.6-1.2 W 功耗口径、binning/to-do list/resolve 全流程图
- [Arm GPU Best Practices Developer Guide — Efficient render passes with Vulkan](https://developer.arm.com/documentation/101897/0304/Fragment-shading/Efficient-render-passes-with-Vulkan) — loadOp/storeOp 语义、transient attachment、反模式清单
- [Jonah Williams — Tile-Based Deferred Renderers (TBDR) Pt 1](https://www.jonahwilliams.dev/mobile_rendering.html) — IMR vs TBR 伪代码、MSAA 在 tile memory 中 resolve、memoryless/lazily allocated 纹理
- [Apple Developer — Rendering a scene with deferred lighting in C++ (Metal)](http://docs.developer.apple.com/documentation/Metal/rendering-a-scene-with-deferred-lighting-in-c++) — TBDR 上的单 pass 延迟渲染、programmable blending、raster order groups
