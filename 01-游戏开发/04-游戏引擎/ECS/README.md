# ECS（Entity Component System）

## 简介

- ECS 是一种以**数据为中心**的架构模式：把"实体是谁"（Entity）、"实体有什么数据"（Component）、"数据怎么被处理"（System）三者彻底分离，广泛用于游戏引擎与高性能仿真（Unity DOTS、Unreal Mass、Bevy、Flecs、EnTT）。
- 解决的核心痛点：OOP 继承体系（`GameObject` 基类不断膨胀、钻石继承、行为难以跨类复用）在大量同质对象上既慢又难维护；ECS 通过组合优于继承 + 连续内存布局同时改善**设计弹性**与**缓存命中率**。
- 关键概念：
  - **Entity**：只是一个整数 ID（通常打包了 generation 代数），不含任何数据和行为。
  - **Component**：纯数据结构（如 `Position`/`Velocity`），按类型集中存储，可任意组合挂到实体上。
  - **System**：只读/写某些组件类型的无状态函数，自动作用于**所有**拥有该组件组合的实体（按组件组合匹配，而非按类匹配）。
- 历史背景：由早期 Entity-Component 框架（组件仍是挂在对象上的属性）演进而来，Adam Martin 等人在 2000s 后期明确提出三分离的 ECS 形态；2010s 随 DOD（Data-Oriented Design）思潮在 Unity DOTS / EnTT / Flecs / Bevy 等实现中工业化。

## 原理详解

### 工作机制（以 sparse set 存储为例）

1. `world.create()` 分配一个递增 entity id，并预留其 generation。
2. `world.add<T>(e, value)` 把组件 value 追加到 T 类型专属 sparse set 的 dense 尾部，并在 sparse 数组记录 `sparse[e] = dense_index`。
3. `world.remove<T>(e)` 用 **swap-remove**：把 dense 尾元素搬到被删位置并回写其 sparse 索引，O(1) 且保持 dense 紧凑。
4. System 运行时做**查询**：取查询涉及的各组件 set 中**最小**的那个遍历，对每个实体检查其余 set 的 sparse 槽位是否命中。
5. 命中的实体依次从各自 dense 数组取出组件，系统函数对组件做计算——全程访问的都是连续数组。

### 核心数据结构（sparse set）

```text
Position 组件的 sparse set（sparse: entity -> dense 下标）:

  entity id:      0    1    2    3    4    5
  sparse[]:     [ 1 ] [ ] [ 0 ] [ ] [ 2 ] [ ]      ← 槽位空 = 无该组件
                   |        |        |
                   v        v        v
  dense[]:     [ 2 | 0 | 4 ]     ← 实体 2 被删后，尾部元素换位填补
  comps[]:     [P2 |P0 |P4 ]     ← 与 dense 一一对应，连续内存

  查询 P+V：遍历较短的 set（如 V 有 2 个），
  对每个候选 e 检查 sparse_P[e] 是否有效。
```

### 查询匹配的三种主流实现（Sander Mertens, ECS FAQ）

| 方式 | 机制 | 代表 |
| --- | --- | --- |
| Archetype（表式） | 相同组件组合的实体存同一张表（列为组件类型、行为实体），查询直接命中表列表，平均查询开销趋近 0，但增删组件要跨表搬移 | Flecs、Unity DOTS、Unreal Mass、Bevy（默认） |
| Sparse set | 每类组件一个 sparse set，查询遍历最小集合并做成员测试，增删 O(1) | EnTT、Shipyard、Bevy（opt-in） |
| Bitset | 每类组件一个数组 + bitset 标记，查询做位运算交集 | EntityX、Specs |
| Reactive | 监听实体变更信号增量维护匹配集合 | Entitas |

### ECS 与数据导向设计（DOD）的关系

ECS ≠ DOD：可以不用 ECS 写 DOD 代码，也可以写出不用 DOD 的 ECS（ECS FAQ 明确区分）。但 ECS 的"组件按类型连续存储"天然适配 DOD 的目标——按访问模式组织数据、利用 CPU 缓存行与 SIMD，因此主流实现都以密集数组为底座。Bevy 0.5 甚至同时提供 Table（archetype，默认快速迭代）与 SparseSet（opt-in，快速增删）两种存储，按组件使用频率选择（Bevy ECS v2 设计文）。

## 对比 / 选型

| 维度 | Archetype | Sparse set | Bitset |
| --- | --- | --- | --- |
| 迭代性能 | 最优（纯连续，SoA 友好） | 有间接寻址，默认略慢 | 与 sparse set 相近 |
| 组件增删 | 慢（跨表复制全部组件） | O(1) swap-remove | O(1) |
| 内存 | 表碎片化（组合爆炸时小表多） | sparse 数组按最大 entity id 分配 | 每类型一份全量 bitset |
| 并行调度 | 友好（实体只属一张表） | 需锁整库或细粒度锁 | 一般 |
| 适用场景 | 组合稳定、迭代密集（大世界模拟） | 组件高频增删（buff/tag 切换） | 中小规模、查询类型多 |

工业实践：Unity 把 ECS 作为 DOTS 三件套（ECS + Burst 编译器 + Job System）的数据层基础；Hardspace: Shipbreaker 报告部分流程从 1 小时降到 100ms（Unity 官方案例页）。

## 环境准备

- 操作系统：任意（demo 为纯计算，无平台依赖）
- C：C99（`gcc -std=c99`）
- Python：3.8+
- Go：1.21+（用到泛型）
- 依赖：无（标准库 / 零依赖）

## 运行方式

### C

```bash
gcc -O2 -Wall -Wextra -std=c99 c/ecs_demo.c -o ecs_demo && ./ecs_demo
```

### Python

```bash
python3 python/ecs_demo.py
```

### Go

```bash
cd go && go run ecs_demo.go
```

## 关键代码片段

三语言实现同一最小 sparse set ECS：`Position`/`Velocity` 两组件、`movement_system` 查询 P+V 组合。核心是 swap-remove 与最小集合查询（对应"原理详解"第 3、4 步）：

```c
/* swap-remove：O(1) 删除，保持 dense 紧凑 */
static void set_remove(ComponentSet *s, Entity e) {
    size_t idx = s->sparse[e];
    size_t last = s->count - 1;
    Entity moved = s->dense[last];
    s->dense[idx] = moved;          /* 尾元素填补空洞   */
    s->comps[idx] = s->comps[last]; /* 组件同步搬移     */
    s->sparse[moved] = idx;         /* 回写搬移者索引   */
    s->count--;
}

/* 查询：遍历较短的 set，检查另一 set 是否命中 */
void movement_system(World *w) {
    ComponentSet *p = &w->positions, *v = &w->velocities;
    ComponentSet *base = (v->count < p->count) ? v : p;   /* 取最小者 */
    for (size_t i = 0; i < base->count; i++) {
        Entity e = base->dense[i];
        if (!set_has(p, e) || !set_has(v, e)) continue;   /* 成员测试 */
        Position *pos = set_get(p, e);                    /* void* 取回   */
        Velocity *vel = set_get(v, e);
        pos->x += vel->x * DT;                            /* system 逻辑 */
        pos->y += vel->y * DT;
    }
}
```

demo 演示流程：8 个实体（偶数号挂 P+V、奇数号只挂 P）→ 跑 3 帧观察只有偶数号移动 → 销毁实体 2 触发 swap-remove → 打印 sparse/dense 数组内部布局。

## 性能与边界

- 时间复杂度（sparse set 路线）：add/remove/has/get 均 **O(1)**；查询 O(min(|A|,|B|))。
- archetype 路线相反：查询近 O(命中数) 但增删需复制该实体的全部组件（Bevy 0.5 benchmark：对带 5 个 4x4 矩阵组件的实体高频增删 1 个矩阵组件，table 存储显著慢于 sparse set 存储）。
- sparse 数组需按最大 entity id 预留空间：实体 id 稀疏时内存放大（EnTT 等实现用分页 sparse 缓解）。
- ECS 擅长线性遍历与动态组件组合，**不擅长**树/空间查询等需要专用数据结构的场景（ECS FAQ 明确指出；空间数据应每帧在系统内重建或用 tag+grid）。

## 注意事项与常见坑

- **迭代中增删组件**：会破坏正在遍历的 dense 数组（swap-remove 改写后续元素）。规避：延迟到帧末的命令队列（Unity 的 `EntityCommandBuffer`、Bevy 的 `Commands`）。
- **悬空 entity**：id 被复用后旧引用指向新实体。生产实现把 `generation` 打包进 id（如低 32 位 id + 高 32 位代数），EnTT/Bevy/Flecs 均如此；本 demo 为教学只用 alive 位。
- **组合爆炸**：archetype 路线下高频切换 tag 会产生大量只含几个实体的小表，查询变慢（Bevy 对高频增删组件建议改 sparse set 存储）。
- **把行为塞进组件**：组件只放数据、系统只放行为，混入方法会退化为 EC 框架，失去查询匹配的意义。
- C 版用 `void*` + `memcpy` 模拟泛型，组件含指针时浅拷贝即可（纯数据 POD）；Python/Go 版分别为动态类型/真泛型，无此问题。

## 参考资料（实际阅读过的权威来源）

- [Sander Mertens — ECS FAQ](https://github.com/SanderMertens/ecs-faq) — Flecs 作者维护的 ECS 权威 FAQ：三角色定义、archetype/sparse/bitset/reactive 四种实现路线、查询匹配三法、ECS 与 DOD 的关系。
- [Bevy 0.5 — ECS Core Rewrite (Bevy ECS v2)](http://bevydocs.lynndotpy.dev/news/bevy-0-5/) — Bevy 官方博文：archetype vs sparse set 的取舍分析、混合存储方案、增删组件 benchmark 描述。
- [skypjack — EnTT: Gaming meets modern C++](https://github.com/skypjack/entt) — sparse set 路线代表实现（用于 Minecraft 等）：views/groups 访问模式、pay-for-what-you-use 设计。
- [Unity — Data-Oriented Technology Stack (DOTS)](https://unity.com/dots) — 官方页面：DOTS = ECS + Burst + Job System 的定位与工业案例（V Rising、Hardspace: Shipbreaker 等）。
