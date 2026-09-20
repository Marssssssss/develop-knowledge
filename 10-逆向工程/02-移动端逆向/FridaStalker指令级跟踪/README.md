# Frida Stalker 指令级跟踪

## 简介

`Interceptor.attach` 只能盯住**已知函数入口/出口**；遇到混淆过的 App、虚函数表调用、或者"不知道该 hook 谁"的场景，就需要 **Stalker** —— 它跟着线程**逐基本块**重新编译执行到的代码，把执行轨迹以 `GumEvent` 二进制流吐给你。这是"先看见全部、再决定 hook 谁"的逆向路径，代价是巨大开销与海量事件。

本目录：

- `stalker_trace.py` — Stalker 行为模型：`GumEvent` 结构布局、事件位掩码、transform 迭代器、trustThreshold / exclude / 事件队列 / call probe
- `selfcheck_stalker.py` — 77 项断言，逐条对照 `gumevent.h` 与 Frida JS API 文档
- `stalker_agent.js` — 可直接 `frida -U -f <pkg> -l` 加载的真实 agent 脚本

## 原理详解

### 1. GumEvent：onReceive 拿到的到底是什么

`onReceive` 收到的是**一坨二进制**，里面是若干个 `GumEvent` 结构体（源码 `frida-gum/gum/gumevent.h`）。事件类型是按位的：

| 常量 | 值 |
| --- | --- |
| `GUM_NOTHING` | 0 |
| `GUM_CALL` | 1 << 0 = 1 |
| `GUM_RET` | 1 << 1 = 2 |
| `GUM_EXEC` | 1 << 2 = 4 |
| `GUM_BLOCK` | 1 << 3 = 8 |
| `GUM_COMPILE` | 1 << 4 = 16 |

`follow()` 的 `events: {call, ret, exec, block, compile}` 就是这张表的开关，五个全开掩码为 31。

各结构体字段与大小（64 位进程，`gpointer` 8 字节对齐）：

| 结构 | 字段 | 大小 |
| --- | --- | --- |
| `GumAnyEvent` | type | 4 |
| `GumCallEvent` | type, location, target, depth | 32 |
| `GumRetEvent` | type, location, target, depth | 32 |
| `GumExecEvent` | type, location | 16 |
| `GumBlockEvent` | type, start, end | 24 |
| `GumCompileEvent` | type, start, end | 24 |

联合体 `GumEvent` 取最大成员，即 32 字节。**别用固定步长去切 `onReceive` 的 blob** —— 必须按第一个 `type` 字段决定本条多长，再步进。

### 2. 五个事件的语义差别

- `call` / `ret`：函数调用与返回，带 `depth`（调用深度），是"谁调了谁"的主线；
- `exec`：**每一条**指令 —— 文档自己写了 "not recommended as it's a lot of data"；
- `block`：基本块粒度，粗粒度执行轨迹，最常用的档位；
- `compile`：块**被编译**时触发，适合做覆盖率。

### 3. transform：真正插桩的地方

`transform(iterator)` 在 Stalker 重新编译某个基本块时被同步调用。默认实现就一行：

```js
while (iterator.next() !== null) iterator.keep();
```

关键点：**不调 `keep()` 的指令会被丢弃**，所以 transform 可以整段替换某些指令。`iterator` 还能 `putCallout()`（同步回调回 JS，能读改寄存器）、`putCmpRegI32()` / `putJccShortLabel()` / `putLabel()` 手搓判断逻辑、`putChainingReturn()` 提前返回。

ARM/ARM64 上有个硬坑：文档注释写明"we may disturb instruction sequences that deal with exclusive stores"，所以只有 `iterator.memoryAccess === 'open'` 时才允许插入噪声代码。本目录的模型里，`memoryAccess` 不为 `open` 时调用 `putCallout` 直接抛错。

热路径上可以把 transform 与事件处理都写成 C（`CModule`），用 `transform: cm.transform, onEvent: cm.process` 挂进去。

### 4. exclude：降噪的唯一正确姿势

`Stalker.exclude(range)` 把某段内存标记为排除：Stalker 遇到跳进去的调用**不再跟进去** —— 你仍能看到入参与返回值，但看不到中间的指令。把 `libc.so` / `libart.so` 这类高频库排除掉，是能把 Stalker 用起来的前提。

### 5. trustThreshold

自修改代码的取舍：`-1` 永不信任（慢）、`0` 从一开始就信任、`N` 执行 N 次后信任，**默认 1**。被"信任"的代码不再重复插桩/重新编译。

### 6. 事件队列

`queueCapacity` 默认 16384 条，`queueDrainInterval` 默认 250ms（每秒排空 4 次）。设为 0 会**关闭周期排空**，此后只能靠 `Stalker.flush()` 手动排空。队列满时会丢事件，这一点文档没承诺不丢——实测长时间跟踪必须放大 `queueCapacity` 或缩短 `queueDrainInterval`。

### 7. call probe 与 invalidate

- `addCallProbe(address, callback[, data])` 返回 id，命中即同步回调（签名同 `onEnter`），`removeCallProbe(id)` 摘掉；`data` 可以是 `NativePointer`，会作为 `user_data` 传进回调。
- `invalidate(address)` 只作废**指定基本块**的已翻译代码，比 `unfollow()` + `follow()` 便宜得多 —— 后者会把所有缓存翻译全部丢弃、重新编译。

### 8. unfollow 之后必须 garbageCollect

文档原话：`garbageCollect()` 是在 `unfollow` 之后的**安全点**释放累积内存，用来避免"刚 unfollow 的线程还在执行它的最后几条指令"造成的竞态。本目录模型因此判定：**仍在 follow 时调用 garbageCollect 是不安全的**。

## 对比：Stalker vs Interceptor

| 维度 | Interceptor | Stalker |
| --- | --- | --- |
| 粒度 | 函数入口/出口 | 基本块 / 指令 |
| 前提 | 必须知道目标地址与签名 | 不需要，跟着线程跑 |
| 数据形态 | JS 回调参数 | `GumEvent` 二进制流 + `Stalker.parse()` |
| 开销 | 低（只钩住点） | 高（整段重新编译） |
| 典型用途 | 抓参数、改返回值 | 覆盖率、调用图、定位未知逻辑 |
| 丢弃指令 | 不支持 | transform 不 keep 即丢弃 |

## 环境

- Python 3.8+（模型自检只用标准库）
- Frida 16+ / 17+（agent 脚本；本机无设备与 server，未实跑）
- 注意：Frida 17 起 ObjC 桥不再内置，需 `npm install frida-objc-bridge` 后 import

## 运行方式

```bash
python selfcheck_stalker.py                       # 77 项断言
frida -U -f com.example.app -l stalker_agent.js --no-pause
```

## 关键代码

```python
# 事件结构体大小：guint 之后要补到 gpointer 对齐
def event_struct_size(kind):
    size = TYPE_W
    if kind in (GUM_CALL, GUM_RET):
        size += _pad(size) + PTR      # location
        size += PTR                   # target
        size += 4                     # gint depth
        size += _pad(size)
        return size

# trustThreshold：-1 永不信任，0 立即信任，N 执行 N 次后信任
def trusted(exec_count, threshold):
    if threshold < 0: return False
    if threshold == 0: return True
    return exec_count >= threshold
```

```js
// 只有 memoryAccess 为 open 时才允许插桩（ARM/ARM64 独占访存）
const canEmitNoisyCode = iterator.memoryAccess === 'open';
if (isAppCode && canEmitNoisyCode && instruction.mnemonic === 'ret') {
  iterator.putCallout(onMatch);
}
iterator.keep();     // 不调 keep() 的指令会被丢弃
```

## 性能边界

- `exec: true` 会产生海量数据，文档明确不推荐；默认是关的。
- 队列满即丢事件；长跟踪要么加大 `queueCapacity`，要么缩短 `queueDrainInterval`，要么只开 `block`/`compile`。
- `trustThreshold = -1` 意味着每块每次都重新编译，开销最大；`0` 最快但对自修改代码不安全。
- transform 与 callout 都可用 `CModule` 写成 C，是热路径上唯一可行的提速手段。

## 注意事项与常见坑

1. **onReceive 与 onCallSummary 只能二选一** —— 文档原话 "Only specify one of the two"；留一个空回调也会付出性能代价。
2. **别按固定步长切 `onReceive` 的 blob** —— 事件是变长的联合体，必须按 `type` 决定步长。
3. **不调 `keep()` 会丢指令** —— 这是特性（可整段替换），不是 bug，但写漏了就是静默改行为。
4. **ARM/ARM64 插桩要验 `memoryAccess`** —— 破坏独占访存序列会导致极难定位的偶发崩溃。
5. **`unfollow` 后要 `garbageCollect`** —— 否则踩到"线程还在执行最后几条指令"的竞态。
6. **改 transform 用 `invalidate` 而不是重新 follow** —— 后者会丢弃全部缓存翻译。
7. **`queueDrainInterval = 0` 之后没有周期排空** —— 忘了 `flush()` 就永远收不到事件。

## 参考资料（实际读过）

- [Frida JavaScript API — Stalker 一节](https://frida.re/docs/javascript-api/) —— follow/unfollow/parse/flush/garbageCollect/invalidate/exclude/addCallProbe/removeCallProbe、events 五个开关、transform 默认实现与 ret 插桩示例、CModule 混合写法、trustThreshold / queueCapacity / queueDrainInterval 三个属性的默认值与取值语义
- [frida-gum 源码 `gum/gumevent.h`](https://github.com/frida/frida-gum/blob/main/gum/gumevent.h) —— `GumEventType` 枚举（GUM_NOTHING / CALL / RET / EXEC / BLOCK / COMPILE）、六个结构体字段与 union 定义
