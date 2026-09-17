# GPU 实例化渲染(Instancing)

## 简介

渲染顶点本身很快,**向 GPU 下达渲染指令不快**:每次 draw call 前的准备工作(绑定 VAO/纹理/设置 uniform)都要走相对缓慢的 CPU→GPU 总线。草地上万片草叶、每片只有几个三角形时,瓶颈是**几千次 draw call 的 CPU 开销**,不是 GPU 的光栅化能力。实例化渲染把 N 次绘制合并为 1 次:顶点数据只发一遍,再用一个调用告诉 GPU 绘制 N 个实例。

- **glDrawArraysInstanced / glDrawElementsInstanced**:多一个 instance count 参数
- **gl_InstanceID**:顶点着色器内建变量,从 0 起为每个实例递增(第 43 个实例 ID = 42)
- **Instanced Array**:顶点属性 + `glVertexAttribDivisor(attr, 1)`,"每实例更新一次"
- **mat4 实例变换**:占 4 个连续顶点属性位置(每属性最大一个 vec4)
- 规模口径(LearnOpenGL):100000 个 576 顶点的小行星 ≈ **5700 万顶点/帧,仅 2 次 draw call**

## 原理详解

### 从逐个绘制到一次绘制

```text
朴素:for i in range(N):                    实例化:
  DoSomePreparations()    # bind VAO/纹理     # 数据只上传一次
  glDrawArrays(...)       # 走 CPU→GPU 总线   glDrawArraysInstanced(..., N)
```

实例间的差异来自两类数据:uniform 数组(用 gl_InstanceID 索引)或 instanced array(顶点属性流)。GPU 渲染全部实例期间**不需要再与 CPU 通信**。

### divisor 机制:属性流的两条泳道

`glVertexAttribDivisor(location, divisor)` 决定属性**何时前进到下一个元素**:

| divisor | 前进时机 | 用途 |
| --- | --- | --- |
| 0(默认) | 每顶点 | 位置、颜色等 per-vertex 数据 |
| 1 | 每实例 | 偏移、变换矩阵等 per-instance 数据 |
| 2 | 每 2 个实例 | 也可表达更粗的分组 |

顶点着色器对(顶点 j, 实例 i)取数:vertex 属性读第 j 个,instance 属性读第 i 个——这正是本 demo `VertexStream.fetch(j, i)` 模拟的硬件行为。

### mat4 要占 4 个属性位置

顶点属性的最大数据量是一个 vec4,mat4 相当于 4 个 vec4:location 3/4/5/6 分别绑定矩阵的 4 列,各自 `glVertexAttribDivisor(·, 1)`;着色器里直接声明 `layout (location = 3) in mat4 instanceMatrix;`,不再需要 model uniform。

### uniform 数组的上限

实例数据走 uniform 数组时受 `GL_MAX_VERTEX_UNIFORM_COMPONENTS` 限制(OpenGL 保证下限 1024;每实例一个 vec2 偏移 → 最多 512 个实例)。**Instanced array 走顶点属性通道,只受显存约束**,这才是大规模实例化的载体。

## 对比 / 选型

| 维度 | 逐个 draw call | Instancing |
| --- | --- | --- |
| draw call 数 | N(小行星带 1001) | 1~2 |
| CPU 准备开销 | N × ~25μs(本 demo 记账:1001 次 ≈ 25ms) | 2 × 25μs |
| 顶点着色次数 | N × V | N × V(**完全相同**) |
| 流畅上限(LearnOpenGL) | ~1000-1500 个小行星 | 100000 个(576 顶点/个) |

注意:实例化**不减少顶点处理量**——5700 万顶点该算还是算;它砍掉的是 CPU 侧的指令开销。适用前提:**同一网格的重复实例**(草地、粒子、森林、星系);不同网格要先合批成一张大网格再谈实例化。

## 环境准备

- Python 3.8+ / Go 1.18+,无第三方依赖

## 运行方式

```bash
python3 python/main.py
go run go/main.go
```

## 关键代码片段

```python
class VertexStream:
    """divisor=0 随顶点前进,divisor=1 随实例前进(GPU 取数的等价模拟)。"""
    def fetch(self, vertex_index, instance_index):
        return self.vertex_data[vertex_index], self.instance_data[instance_index]

def mat4_columns(transform):
    """mat4 拆 4 个顶点属性:第 3 列(location 6)携带平移。"""
    ox, oy, oz = transform["offset"]
    s = transform["scale"]
    return [(s, 0, 0, 0), (0, s, 0, 0), (0, 0, s, 0), (ox, oy, oz, 1.0)]
```

## 性能与边界

- 记账模型:CPU 准备 25μs/draw call、GPU 5ns/顶点(数量级建模用,非实测);1000 小行星下 CPU 准备 25.0ms vs 0.050ms(**500 倍差距**)
- 帧预算换算:60fps 只有 16.6ms,1001 次 draw call 的 CPU 准备已经吃光预算——实例化把这 25ms 变成 50μs
- uniform 数组方案 <1000 实例可用;更大规模必须 instanced array

## 注意事项与常见坑

- **gl_InstanceID 从 0 开始**,不是 1(第 43 个实例的 ID 是 42)
- **uniform 数组只适合小规模**(<1000),大量实例必须 instanced array
- **别对不同网格硬套实例化**:前提是同一 mesh;异构网格先做 atlas/合批
- **glVertexAttribDivisor 是 VAO 状态**,忘了设 1 时 instance 属性会退化成 per-vertex(每顶点取一次,实例数据只用了第一个)
- 引擎侧的进阶路线:间接绘制 + compute shader 剔除/LOD → GPU-Driven Rendering

## 参考资料(实际阅读过的权威来源)

- [LearnOpenGL — Instancing](https://learnopengl.com/advanced-opengl/instancing) — draw call 瓶颈成因、gl_InstanceID、instanced array 与 glVertexAttribDivisor、mat4 的 4 属性拆分、100000×576 顶点 = 2 次 draw call 的实测规模
- [LearnOpenGL 中文版 — 实例化](https://learnopengl-cn.github.io/04%20Advanced%20OpenGL/10%20Instancing/) — 同教程中文翻译,草叶场景的动机描述
- [OpenGL Wiki(riptutorial 节选)— Instancing by Vertex Attribute Arrays](https://riptutorial.com/de/opengl/example/26987/instanzierung-durch-vertex-attribut-arrays) — divisor 状态归属 VAO、divisor=0/1 的语义表
