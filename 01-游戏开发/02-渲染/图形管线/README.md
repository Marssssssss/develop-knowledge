# 图形管线

CPU 端组织渲染指令（DrawCall），GPU 端按顶点/几何/片元阶段处理。

## 关键概念

- **Draw Call**：CPU 发起的渲染指令调用，越多越慢（瓶颈通常在 CPU）
- **顶点着色器**：变换顶点位置到裁剪空间
- **片元着色器**：计算每个像素颜色
- **几何/计算着色器**：DX11+/Vulkan 才有，用于复杂效果
- **延迟渲染（Deferred Shading）**：先渲染 G-Buffer，再统一光照
- **前向渲染（Forward）**：逐物体逐光源计算
- **Tile-Based GPU 架构**：移动端必须考虑

## 待研究

- [ ] Forward vs Deferred vs Forward+ 选型
- [ ] GPU Instancing 减少 Draw Call
- [ ] SRP Batcher（URP）
- [ ] Compute Shader 的并行任务