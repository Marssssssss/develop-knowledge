# 计算着色器执行模型(Compute Shader)

## 简介

计算着色器是 Direct3D 11 引入的可编程阶段(DirectCompute 技术),用 HLSL 编写,但**不在图形管线的固定位置上**:它做 GPU 上的通用并行计算,提供图形管线没有的**内存共享与线程同步**原语。理解它的关键是三层线程组织——调度(dispatch)→ 线程组(thread group)→ 线程(thread),以及 groupshared 内存 + 屏障的协作编程模型。

- **Dispatch(gx,gy,gz)**:CPU 发起,启动 gx×gy×gz 个线程组
- **[numthreads(x,y,z)]**:着色器声明每组线程数,组内线程可共享内存
- **四个系统值标识**:SV_GroupID / SV_GroupThreadID / SV_DispatchThreadID / SV_GroupIndex
- **groupshared + GroupMemoryBarrierWithGroupSync**:组内共享内存与屏障
- **原子指令(InterlockedAdd 等)**:多线程写同一地址不丢更新
- 现代图形管线把大量任务放在 compute pass 里:Forward+ 的光源剔除、粒子模拟、GPU 驱动的剔除与 LOD

## 原理详解

### 三层线程组织与四个标识

```
Dispatch(5,3,2) ── 30 个线程组(执行顺序未定义)
  └─ numthreads(10,8,3) ── 每组 240 线程
       例:GroupThreadID = (7,5,0) 的线程
           GroupID          = (2,1,0)
           DispatchThreadID = (2,1,0)*(10,8,3) + (7,5,0) = (27,13,0)
           GroupIndex       = 0*10*8 + 5*10 + 7 = 57
```

| 系统值 | 含义 | 典型用途 |
| --- | --- | --- |
| SV_GroupID | 本组在 dispatch 网格中的坐标 | 每组处理一个 tile/分块 |
| SV_GroupThreadID | 线程在组内的坐标 | groupshared 写入位置 |
| SV_DispatchThreadID | 全局坐标 = GroupID×numthreads + GroupThreadID | 全局输出下标 |
| SV_GroupIndex | 组内线性索引 = z·X·Y + y·X + x | groupshared 一维数组下标 |

### 硬件限制(cs_5_0 vs cs_4_x)

| 限制 | cs_4_x(FL10) | cs_5_0(FL11) |
| --- | --- | --- |
| 每组最大线程 | 768 | **1024** |
| numthreads 的 Z 维 | 1 | 64 |
| Dispatch 每维最大组数 | 65535 | 65535 |
| groupshared | 16 KB | **32 KB** |
| 原子指令 | 无 | 有(InterlockedAdd/Min/Max/CAS…) |
| 可绑定 UAV | 1(且仅 RWStructuredBuffer/RWByteAddressBuffer) | 8(含类型化 RWTexture) |

注意 X、Y 维的 numthreads 上限(cs_5_0 为 1024)与"每组总线程 ≤1024"是两条独立限制:`(33,33,1)` 总数 1089 超限,`(64,1,65)` 则是 Z 维超限。

### groupshared 归约:屏障为什么必需

组内 256 线程做求和的标准写法(树形归约):

```
阶段 0:每线程写 shared[GI] = 输入[GI];     ← GroupMemoryBarrierWithGroupSync
阶段 k:线程 i(shared[i] 有效时)做
        shared[i] += shared[i + stride]     ← 屏障,翻倍半步
```

每个线程**只能保证看到屏障之前其他线程写入的值**。漏一个屏障,下一级就会读到未更新的旧值——数据竞争在 GPU 上的形态。本 demo 用"阶段化执行"模拟该语义:阶段内顺序任意、阶段边界即屏障。

### 原子指令:丢失的更新

两个线程对同一计数器 `counter += v`:非原子时读-改-写可以交错(都读到 0,分别写 v₁ 和 v₂,只留一个——**丢更新**);`InterlockedAdd` 把读改写变成不可分割的硬件操作。cs_4_x 没有原子指令,这也是它只能跑受限负载的原因之一。

## 对比 / 选型

| 维度 | 像素着色器 | 计算着色器 |
| --- | --- | --- |
| 输入组织 | 光栅化决定片元 | 任意(dispatch 网格) |
| 共享内存 | 无 | groupshared ≤32KB/组 |
| 线程同步 | 无 | 组内屏障 + 原子指令 |
| 分散写 | UAV 受限 | RW 资源全量支持 |
| 典型用途 | 光照/材质 | 剔除、粒子、后处理、模拟 |

## 环境准备

- Python 3.8+ / Go 1.18+,无第三方依赖(执行模型模拟)

## 运行方式

```bash
python3 python/main.py
go run go/main.go
```

## 关键代码片段

```python
def run_reduction(self, values):
    """groupshared 树形归约:每级折半,级间屏障。"""
    for i in range(n):
        self.shared[i] = values[i]          # 阶段 0:每线程写自己的值
    self.barrier()                           # ← 缺了这个就读到旧值
    stride = n // 2
    while stride > 0:
        for i in range(stride):
            self.shared[i] += self.shared[i + stride]
        self.barrier()                      # ← 每级归约之间都要屏障
        stride //= 2
    return self.shared[0]                   # 线程 0 写出组结果
```

## 性能与边界

- 全量枚举自检:Dispatch(5,3,2)×numthreads(10,8,3) = 3600 线程,DispatchThreadID 无重复无洞,GroupIndex 与组内坐标一一对应
- 树形归约 O(n log n) 次 shared 访问、log₂n 次屏障;每组 1024 线程只需 10 级
- groupshared 是稀缺资源:32KB 每组,大缓冲要么减小组线程数要么分多次 dispatch

## 注意事项与常见坑

- **numthreads 的三个数不是独立上限**:除了 X/Y/Z 各维上限,还有"每组总数 ≤1024"的总闸
- **屏障漏一处就数据竞争**,而且**不报错**——归约结果偶发地差一点是最典型的症状
- **不要把 groupshared 初始化写进"if(GI==0)"后又紧跟使用**:初始化后必须先屏障
- **DispatchThreadID 与全局资源下标对齐**:组内坐标换算错位是 off-by-one 的重灾区(GroupIndex 公式里 z 在最高位)
- cs_4_x 的 Z 维只能是 1,且无原子指令——跨 feature level 的代码要降级处理

## 参考资料(实际阅读过的权威来源)

- [Microsoft Learn — Compute Shader Overview](https://learn.microsoft.com/en-us/windows/win32/direct3d11/direct3d-11-advanced-stages-compute-shader) — cs_4_x / cs_5_0 全部限制数字(768/1024、16KB/32KB、UAV 数、原子支持)
- [Microsoft Learn — numthreads attribute (HLSL)](https://learn.microsoft.com/en-us/windows/desktop/direct3dhlsl/sm5-attributes-numthreads) — Dispatch(5,3,2)+numthreads(10,8,3) 的官方图解与 DispatchThreadID(27,13,0)/GroupIndex 57 的推导
- [Microsoft Learn — ID3D11DeviceContext::Dispatch](https://learn.microsoft.com/en-us/windows/win32/api/d3d11/nf-d3d11-id3d11devicecontext-dispatch) — 每维 ≤65535 组、FL10 时 Z 必须为 1
- [Ishmukhametov — Efficient Tile-Based Deferred Shading Pipeline (DigiPen 硕士论文, Appendix A)](https://www.digipen.edu/sites/default/files/public/docs/theses/denis-ishmukhametov-master-of-science-in-computer-science-thesis-efficient-tile-based-deferred-shading-pipeline.pdf) — compute shader 在 tiled deferred 光源剔除中的应用与线程组织图解
