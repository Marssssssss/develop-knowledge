# 游戏渲染

实时渲染涉及图形管线、着色器、光照、阴影、后处理等多个环节。

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [图形管线/](./图形管线/) | 顶点/片元/几何/计算着色器、延迟/前向渲染 |
| [着色器/](./着色器/) | GLSL / HLSL / WGSL、Shadertoy 风格 |
| [光照与阴影/](./光照与阴影/) | PBR、光照模型、Shadow Map、SSAO |

## demo 索引

- ✅ [图形管线/深度缓冲/](./图形管线/深度缓冲/) — Z-Buffer 与 Z-Fighting（C / Python / Go）
- ✅ [图形管线/延迟渲染GBuffer/](./图形管线/延迟渲染GBuffer/) — 延迟着色 G-buffer 与 light volume（Python / Go）
- ✅ [图形管线/ForwardPlus分块光源剔除/](./图形管线/ForwardPlus分块光源剔除/) — Forward+/clustered 光源剔除（Python / Go）
- ✅ [图形管线/GPU实例化Instancing/](./图形管线/GPU实例化Instancing/) — 实例化渲染与 draw call 合批（Python / Go）
- ✅ [图形管线/TileBasedGPU架构/](./图形管线/TileBasedGPU架构/) — 移动端 TBDR 与 loadOp/storeOp（Python / Go）
- ✅ [图形管线/计算着色器执行模型/](./图形管线/计算着色器执行模型/) — 线程组织/groupshared/屏障与原子（Python / Go）

## 待研究知识点

- [ ] PBR 材质
- [ ] IBL 烘焙
- [ ] 全局光照（Light Probe / Voxel GI）
- [ ] 实时光线追踪（DXR / Vulkan RT）