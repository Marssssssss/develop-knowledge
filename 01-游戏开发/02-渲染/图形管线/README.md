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

## demo 索引

- ✅ [深度缓冲/](./深度缓冲/) — Z-Buffer 与 Z-Fighting：C / Python / Go 三语言软件光栅化（依据 Khronos OpenGL Wiki / Marburg 讲义）
- ✅ [延迟渲染GBuffer/](./延迟渲染GBuffer/) — 延迟着色两 pass 与 G-buffer 布局、light volume 半径反解、交叉点：C 无 / Python / Go（依据 LearnOpenGL）
- ✅ [ForwardPlus分块光源剔除/](./ForwardPlus分块光源剔除/) — tile 视锥四平面、球-视锥+深度区间剔除、opaque/transparent 双列表、clustered 深度切片：Python / Go（依据 3dgep）
- ✅ [GPU实例化Instancing/](./GPU实例化Instancing/) — draw call 记账、gl_InstanceID、divisor 取数模拟、mat4 四属性拆分：Python / Go（依据 LearnOpenGL）
- ✅ [TileBasedGPU架构/](./TileBasedGPU架构/) — binning/to-do list、loadOp/storeOp 带宽记账、片上 MSAA resolve：Python / Go（依据 Arm 文档）
- ✅ [计算着色器执行模型/](./计算着色器执行模型/) — 四系统值标识、cs_5_0/cs_4_x 限制、groupshared 树形归约与屏障、原子指令：Python / Go（依据 Microsoft Learn）

## 待研究

- [ ] SRP Batcher（URP）
- [ ] 几何着色器与网格着色器（Mesh Shader / meshlet）
- [ ] GPU-Driven Rendering（间接绘制 + compute 剔除/LOD）