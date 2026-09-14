# GIL、并发模型与自由线程(PEP 703)

## 简介

CPython 的**全局解释器锁(GIL)** 保证"同一时刻只有一个线程在执行 Python 字节码",
它是解释器内部状态(尤其是对象引用计数)的简单保护方案,代价是 **CPU 密集任务用多线程拿不到多核**。
PEP 703 提出把它变成可选项(`--disable-gil` 构建)。

- 一句话:GIL 让多线程只能重叠 **I/O 等待**,不能重叠 **CPU 计算**;要并行计算得用多进程或等自由线程。
- 关键概念:
  - **GIL**:全局锁,"prevents multiple threads from executing Python code at the same time"
  - **切换间隔(`sys.setswitchinterval`)**:时间片的理想长度(秒),实际由操作系统决定何时切换
  - **释放点**:执行 I/O、调用会释放 GIL 的 C 函数时线程会主动让出锁
  - **自由线程(free-threading)**:`--disable-gil` 构建,用偏向引用计数 + 每对象锁替代 GIL
  - **每解释器 GIL(PEP 684)**:sub-interpreters 各有自己的 GIL,与 PEP 703 互为补充方案
- 历史:GIL 自 CPython 早期就存在;PEP 684(每解释器 GIL)与 PEP 703(可选 GIL)在 3.12/3.13 落地;
  PEP 703 由指导委员会于 2023-10-24 接受,附带"**gradual rollout / 可回退**"条件。

## 原理详解

### 1. GIL 的定义与代价(PEP 703 原文)

> "CPython's global interpreter lock (\"GIL\") prevents multiple threads from executing
> Python code at the same time. The GIL is an obstacle to using multi-core CPUs from Python
> efficiently."

PEP 703 列出的具体痛点:
- 难以表达 inter-operator parallelism:"even with fewer than 10 threads the GIL becomes the
  bottleneck"(DeepMind 工程师原话);
- 库被迫设计绕路 API(PyTorch `DataLoader`、scikit-learn 的 `joblib/loky`);
- `multiprocessing` 作为替代,但代价高:"Starting a thread takes ~100 us, while spawning a
  subprocess takes ~50 ms (50,000 us) due to Python re-initialization."
  并且"许多 C/C++ 库支持多线程访问,却不支持跨进程使用"。

### 2. 切换间隔:`sys.getswitchinterval / setswitchinterval`

文档:"This floating-point value determines the ideal duration of the 'timeslices' allocated
to concurrently running Python threads." 以及"the actual value can be higher ... which thread
becomes scheduled at the end of the interval is the operating system's decision.
**The interpreter doesn't have its own scheduler.**" 本机实测默认值 `0.005` 秒(`getswitchinterval`)。

### 3. 切换点在哪里(本 demo §4 的关键事实)

**GIL 只保证单条字节码不被切开,不保证复合操作的原子性。** 而且现代 CPython 只在
**带中断检查的字节码**上才可能切换线程,典型就是循环回跳 —— dis 文档说得很直白:

- `JUMP_BACKWARD`:"Decrements bytecode counter by *delta*. **Checks for interrupts.**"
- `JUMP_BACKWARD_NO_INTERRUPT`:"**Does not check for interrupts.**"

所以本 demo 实测到了两种截然不同的结果(4 线程,每线程若干次自增):

| 场景 | 丢失更新 | 解释 |
| --- | --- | --- |
| 紧凑循环 `counter += 1`,切换间隔 5ms / 1µs | **0 / 4,000,000** | 循环体内没有中断检查点,几乎不会被从中间切开 |
| 临界区内插入 `time.sleep(0)`(释放 GIL 并让出) | **149,496 / 200,000** | 临界区里出现了必然的切开点,丢失率约 75% |

结论:`+=` 在紧凑循环里"看起来安全"是**实现细节的副作用**,不是语言保证 ——
跨线程共享可变状态必须用 `Lock` / `queue` / 原子结构。

### 4. 三种并发手段的实测对照(本机 16 逻辑核)

| 场景 | 现象 | 结论 |
| --- | --- | --- |
| CPU 密集 · 1 线程 | 基准(本 demo 0.445s) | — |
| CPU 密集 · 4 线程 | 1.770s,**加速比 1.01x** | GIL 串行化,线程只增加切换开销 |
| CPU 密集 · 4 进程 | 0.691s,**加速比 2.58x** | 每进程独立解释器 + 独立 GIL,真并行 |
| I/O 密集 · 8 线程各 sleep 0.25s | 墙钟 **0.254s**(重叠比 7.9x) | 等待期间释放 GIL,线程完全够用 |

进程版加速比没到 4x,是因为 4 个任务太短,`ProcessPoolExecutor` 的启动与结果回传占了固定开销
—— N 越大越明显,这正是 PEP 703 里"spawn 代价 ~50ms"的具体体现。

### 5. PEP 703 的技术方案

实现改动分四类:**引用计数 / 内存管理 / 容器线程安全 / 锁与原子 API**:

1. **偏向引用计数(BRC)**:对象有 owning thread,owner 用非原子指令改本地计数
   (`ob_ref_local`),其他线程用原子指令改共享计数(`ob_ref_shared`);对象头加 `ob_tid`、
   `ob_mutex`、`ob_gc_bits`;状态机四态(default / weakrefs / queued / merged)。
2. **延迟引用计数**:顶层函数、代码对象、模块、方法这类被多线程频繁访问的对象,
   栈上的 push/pop 不改计数,只能在 GC 停顿时算准 —— 代价是它们**只能在 GC 周期里释放**。
3. **immortal 对象**:interned 字符串、小整数、静态类型对象、`True/False/None` 把
   `ob_ref_local` 设为 `UINT32_MAX`,`Py_INCREF/DECREF` 变成 no-op(参考 PEP 683)。
4. **mimalloc 替代 pymalloc**:通用线程安全分配器,小对象性能好,并支撑无锁乐观读。
5. **容器线程安全**:每个 list/dict/set 一把轻量锁;`list[idx]`、`dict[key]` 走**乐观无锁快路径**,
   失败回退加锁路径 —— "The proposed technique is similar to read-copy update (RCU)";
   引入 `Py_BEGIN_CRITICAL_SECTION` 等临界区宏避免死锁。
6. **GC 改动**:cycle detection 用 stop-the-world 保证计数稳定,并改为**非分代**收集。

构建与运行:

- `configure --disable-gil` → 定义 `Py_GIL_DISABLED`,ABI 标签加字母 `'t'`(threading);
- "**The global interpreter lock will remain the default** for CPython builds and python.org
  downloads."(GIL 仍是默认);
- 运行期可用环境变量 `PYTHONGIL=0/1` 或模块槽 `Py_mod_gil` 覆盖;
- 代价:pyperformance 上单线程 **约 6%(Skylake)/ 5%(Zen 3)**,多线程 8%/7%,
  "The largest contribution to execution overhead is biased reference counting followed by
  per-object locking."且**不影响默认(未禁用 GIL)构建的性能**;
- 兼容性:自由线程构建与标准构建、stable ABI **不兼容**(对象头变了),C 扩展必须重新编译;
  分发期会同时存在两套 ABI 的扩展。

### 6. 与 PEP 684(每解释器 GIL)的关系

> "The recently accepted PEP 684 proposes a per-interpreter GIL to address multi-core
> parallelism. This would allow parallelism between interpreters in the same process, but
> places substantial restrictions on sharing Python data between interpreters."
> "It is feasible to implement both PEPs in CPython at the same time."

两者都针对多核并行,取舍不同:684 靠"多解释器 + 数据隔离",703 靠"去掉全局锁 + 细粒度锁"。

## 对比 / 选型

| 需求 | 首选 | 理由 |
| --- | --- | --- |
| 网络/磁盘 I/O 并发 | `threading` / `asyncio` | 等待时释放 GIL,线程足够;协程更省内存 |
| 纯 CPU 计算加速 | `multiprocessing` / `concurrent.futures.ProcessPoolExecutor` | 每进程独立 GIL,线性吃多核(注意序列化与启动开销) |
| 需要 C 扩展 + 真并行 | 自由线程构建(3.13 起实验性)或 PyPy/其他运行时 | 需重建所有 C 扩展,生态尚未跟上 |
| 隔离优先(插件、多租户) | `sub-interpreters`(PEP 684) | 同进程内多解释器,数据默认不共享 |

## 环境准备

- 操作系统:任意(Windows 下 `ProcessPoolExecutor` 用 spawn,启动更慢)
- 语言版本:Python 3.13+(`sys._is_gil_enabled`);本 demo 实测 3.13.14 标准构建
- 依赖:仅标准库。跨语言对照 `gil_demo.go` 需要 Go 1.21+(未在本机编译)

## 运行方式

```bash
python gil_demo.py          # 约 5 秒
go run gil_demo.go          # 对照:GOMAXPROCS=NumCPU 时可达 ~核数 倍加速
GOMAXPROCS=1 go run gil_demo.go
```

## 关键代码片段

线程 vs 进程的对照测量(main 里串起整条链路):

```python
def run_threads(fn, count):
    threads = [threading.Thread(target=fn) for _ in range(count)]
    begin = time.perf_counter()
    for t in threads: t.start()
    for t in threads: t.join()
    return time.perf_counter() - begin

thread_time = run_threads(cpu_work, 4)                      # 受 GIL 限制 -> 加速比 ≈1x
with futures.ProcessPoolExecutor(max_workers=4) as pool:    # 每进程独立 GIL -> 加速比 >1x
    list(pool.map(cpu_work, [WORK] * 4))
```

制造"临界区被切开"的必然机会,用来暴露复合操作的非原子性:

```python
def racy():
    nonlocal counter
    for _ in range(per_thread):
        tmp = counter
        time.sleep(0)      # 释放 GIL 并立即让出:临界区中间出现了切换点
        counter = tmp + 1  # 读-改-写被拆开,别的线程可能已经改过 counter
# 实测:200,000 次自增丢了 149,496 次(约 75%)
```

## 性能与边界

- GIL 的成本主要体现在**CPU 密集多线程**:加速比不升反降(切换开销)。
- 释放点决定了 I/O 密集场景能不能重叠:I/O、`time.sleep`、部分 C 扩展会释放 GIL。
- `sys.setswitchinterval` 调小 → 响应更均匀但总吞吐下降(切换变频繁);
  调大 → 切换少但单线程可能长时间独占(对本机 I/O 响应不利)。
- 自由线程构建的边界:与标准构建 **ABI 不兼容**;借用引用类 C API 需要换成返回新引用的版本;
  自定义分配器必须委托给底层分配器(不能整体替换);Python 对象必须经标准 API 分配。
- PEP 703 只覆盖"3.13 提供 `--disable-gil` 开关"这一步,路线图里**运行时控制**(约 2026-2027)
  与**默认禁用 GIL**(约 2028-2030)都还在 Open Issue 里。

## 注意事项与常见坑

1. **别把"紧凑 `+=` 没丢数据"当成线程安全**:本 demo §4 证明切换点在循环回跳处,
   `+=` 恰好躲过去了;一旦临界区内出现 sleep/I/O/C 调用,丢失立刻出现(实测 75%)。
2. **多进程要小心开销与序列化**:任务太短时进程池的启动/回传会吃掉全部收益(本 demo 4 任务只到 2.58x);
   数据要可 pickle,大对象建议走共享内存。
3. **`sys.getswitchinterval()` 的返回值只是"理想时长"**,不是保证值;实际调度由 OS 决定,
   解释器甚至没有自己的调度器。
4. **别用线程数去"调优" CPU 密集任务**:线程多了只会增加切换与内存占用,吞吐不变。
5. **自由线程构建不等于"免费加速"**:单线程有 5–8% 开销,且 C 扩展生态要重新编译才可用。
6. **不要试图"手动获取 GIL"** 来写业务代码:`sys.setswitchinterval`、`_thread` 之类的低层手段
   只适合诊断;正确做法是选对并发模型 + 用 `Lock`/`queue` 保护共享状态。

## 参考资料(实际阅读过的权威来源)

- [PEP 703 – Making the Global Interpreter Lock Optional in CPython](https://peps.python.org/pep-0703/)
  —— GIL 定义与代价、偏向引用计数/延迟引用计数/immortal/mimalloc/每对象锁、
  `--disable-gil`、ABI `'t'`、`PYTHONGIL`、性能开销数字、与 PEP 684 的关系、迁移与分发挑战
- [sys — `getswitchinterval` / `setswitchinterval` / `getrecursionlimit`](https://docs.python.org/3/library/sys.html)
  —— 切换间隔语义、默认值与"解释器没有自己的调度器"、`getrefcount` 的 immortal 说明
- [dis — `JUMP_BACKWARD` / `JUMP_BACKWARD_NO_INTERRUPT`](https://docs.python.org/3/library/dis.html)
  —— "Checks for interrupts" 的原文,解释切换点为何出现在回跳
- [PEP 684 – A Per-Interpreter GIL](https://peps.python.org/pep-0684/)
  —— 与 PEP 703 并列的另一条多核路线及其数据共享限制
- [PEP 683 – Immortal Objects, Using a Fixed Refcount](https://peps.python.org/pep-0683/)
  —— PEP 703 中"immortal 化"方案的前置工作
- `gil_demo.go` —— 对照实现:Go 的 goroutine 由 runtime 调度到多 OS 线程,无需 GIL
