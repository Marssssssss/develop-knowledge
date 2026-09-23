# Redis 事件循环（ae.c）与多线程 I/O

把 Redis 的网络事件框架 `ae.c` 与 6.0 起的多线程 I/O 常量/判定函数做成**可断言**的最小模型：
事件派发顺序、`AE_BARRIER` 反转、时间事件的自毁与 `refcount` 保护、poll 超时如何由最近定时器算出，
以及 `isCopyAvoidPreferred()` 的 7 条判定分支。

## 事实来源

所有数值与分支都来自下列**实际读过的**源码（分支 `redis/redis@unstable`），未在官方没写定量处编造数值：

| 内容 | 位置 |
| --- | --- |
| `AE_READABLE/WRITABLE/BARRIER`、`AE_FILE_EVENTS/TIME_EVENTS/ALL_EVENTS/DONT_WAIT/CALL_BEFORE_SLEEP/CALL_AFTER_SLEEP`、`AE_NOMORE`、`AE_DELETED_EVENT_ID` | `src/ae.h:22-38` |
| `aeEventLoop` / `aeFileEvent` / `aeTimeEvent` 结构 | `src/ae.h` |
| 初始化：`timeEventNextId=0`、`maxfd=-1`、`nevents=min(setsize,INITIAL_EVENT)` | `src/ae.c:47-78` |
| `aeCreateFileEvent` 扩容（`nevents*2`，被 `setsize` 截断） | `src/ae.c:145-180` |
| `aeDeleteFileEvent`：删 `WRITABLE` 时连 `BARRIER` 一起清、`maxfd` 回扫 | `src/ae.c:181-203` |
| `aeCreateTimeEvent` / `aeDeleteTimeEvent` | `src/ae.c:218-240` / `241-256` |
| `usUntilEarliestTimer`：跳过已删除事件、全删返回 `-1` | `src/ae.c:263-281` |
| `processTimeEvents`：`refcount` 保护、`maxId` 防重入、`finalizerProc` | `src/ae.c:284-363` |
| `aeProcessEvents`：`beforesleep/aftersleep`、`AE_BARRIER` 反转、`fired` 去重 | `src/ae.c:365-495` |
| `aeMain`：固定传 `ALL_EVENTS\|CALL_BEFORE_SLEEP\|CALL_AFTER_SLEEP` | `src/ae.c:497-503` |
| `IO_THREADS_MAX_NUM=128`、`COPY_AVOID_MIN_IO_THREADS=7`、`COPY_AVOID_MIN_STRING_SIZE=16384/65536` | `src/server.h:218-220,243` |
| `CLIENT_PUSHING`、`CLIENT_TYPE_NORMAL/MASTER` | `src/server.h:466,546,549` |
| `OBJ_ENCODING_RAW=0`、`OBJ_REFCOUNT_BITS=23`、`OBJ_SHARED/STATIC_REFCOUNT` | `src/object.h:76,98-101` |
| `isCopyAvoidPreferred()`（7 条判定） | `src/networking.c:1330-1353` |
| `io_threads_num > 1` 才把客户端分派给 I/O 线程 | `src/networking.c:1722` |
| `io-threads-do-reads` 已进入废弃配置表 | `src/config.c:501-506` |
| 线程数建议「4 核用 3、8 核用 7」、默认关闭 | `redis.conf:1408-1435` |

## 目录结构

```
python/ae_const.py        ae.h 常量（单独放，避免 ae 与 ae_time 循环 import）
python/ae.py              事件循环主体 + 文件事件
python/ae_time.py         时间事件（usUntilEarliestTimer / processTimeEvents / 增删）
python/io_threads.py      多线程 I/O 常量与拷贝规避判定
python/selfcheck_ae.py    文件事件 54 条断言
python/selfcheck_time.py  时间事件 31 条断言
python/selfcheck_io.py    多线程 I/O 48 条断言
python/main.py            演示脚本
go/{ae.go, ae_time.go, io_threads.go, main.go, go.mod}   Go 转写
```

## 运行

```bash
cd python && python main.py
cd python && python selfcheck_ae.py && python selfcheck_time.py && python selfcheck_io.py
cd go && go run ae.go ae_time.go io_threads.go main.go
```

## 机制

### 1. 一次 `aeProcessEvents` 做什么

```
flags 里既无 FILE 也无 TIME → 直接返回 0
↓
maxfd != -1  或  (有 TIME 且没有 DONT_WAIT)  才调用 aeApiPoll
↓  beforesleep(若带 CALL_BEFORE_SLEEP)
↓  算超时：DONT_WAIT → 0；有 TIME → usUntilEarliestTimer；否则 tvp=NULL(无限等待)
↓  aeApiPoll
↓  aftersleep(若带 CALL_AFTER_SLEEP)
↓  逐个派发就绪 fd（无 AE_FILE_EVENTS 时把结果丢掉）
↓  处理时间事件
```

注意 **参数 `flags` 优先级高于 `eventLoop->flags`**：`beforesleep` 里改 `eventLoop->flags` 能生效，
但只要调用方在 `flags` 里带了 `AE_DONT_WAIT`，无论 `beforesleep` 怎么改都是零超时（`ae.c:378-386`）。

### 2. `AE_BARRIER` 反转读写顺序

默认先读后写——读命令处理后常常能立刻在同一次循环里把回复写出去。
带 `AE_BARRIER` 时反过来：**先写后读**，用于「必须在回复客户端之前做 `fsync` 之类的事」。

### 3. `fired` 去重

同一个 fd 上读写回调**是同一个函数指针**时，只调用一次：
- 无 `BARRIER`：调 `rfileProc` 后 `fired=1`，写分支因 `wfileProc == rfileProc` 被跳过 → 只读一次
- 有 `BARRIER`：先调 `wfileProc`（`fired=0`），invert 分支里的读又被同样的条件挡掉 → 只写一次

另外每次回调后都会**重新读一次 `fe->mask`**（`ae.c` 里显式 `fe = &eventLoop->events[fd]`），
因为前一个回调可能已经把本 fd 的事件删了。自检里用「读回调中删掉 `WRITABLE`」验证了这一点。

### 4. 时间事件

| 行为 | 规则 |
| --- | --- |
| 返回值 | 返回毫秒数 → 重排到 `now + retval*1000`；返回 `AE_NOMORE` → 只打 `id = -1` 标记 |
| 释放时机 | 标记后**下一轮** `processTimeEvents` 才真正释放并调用 `finalizerProc` |
| `refcount` | `timeProc` 执行期间 `refcount=1`；递归进入时该事件不会被释放 |
| 重排基准 | 用的是**回调返回后重新取**的 `now`，不是进入回调时的 `now` |
| `maxId` | 入口处取 `timeEventNextId-1`，`id > maxId` 的事件本轮跳过 |
| 新事件位置 | `aeCreateTimeEvent` 总是插在**链表头** |

`maxId` 那条检查在正常路径下是**死代码**——因为新定时器总插在头部，当前遍历指针已经越过它了。
源码自己也在注释里说明了这点（「this check is currently useless」）。自检中通过**人为回退**
`timeEventNextId` 来白盒构造 `id > maxId`，确认该分支确实按预期跳过。

### 5. poll 超时来自最近的定时器

`usUntilEarliestTimer` 遍历链表取 `when` 最小者，跳过 `id == AE_DELETED_EVENT_ID`；
若链表中只剩已删除事件，返回 `-1`（`tvp = NULL`，无限等待）——**不会**解引用空指针。
`now >= when` 时返回 `0`，即立刻返回不阻塞。

### 6. 多线程 I/O

- `IO_THREADS_MAX_NUM = 128`，默认 `io_threads_num = 1`（即单线程）。
- 老版本需要 `io-threads-do-reads yes` 才把读/解析也卸载到 I/O 线程；该配置已进入
  `config.c` 的废弃表，**现在只要 `io-threads > 1`，读写与协议解析都由 I/O 线程承担**。
- `redis.conf` 建议：至少 4 核才开，且留一个核给主线程（4 核 → 3，8 核 → 7）。
- 回复大字符串时的**拷贝规避**判定 `isCopyAvoidPreferred()`（`networking.c:1330`），按顺序否决：

| 顺序 | 条件 | 结果 |
| --- | --- | --- |
| 1 | 无连接（fake client）或功能未开启 | 拷贝 |
| 2 | 客户端不是 `CLIENT_TYPE_NORMAL` | 拷贝 |
| 3 | 带 `CLIENT_PUSHING` | 拷贝 |
| 4 | 非 `OBJ_ENCODING_RAW`，或 `refcount >= OBJ_FIRST_SPECIAL_REFCOUNT`（8388606） | 拷贝 |
| 5 | `io_threads_num >= 7` | **任意长度都走引用** |
| 6 | `io_threads_num == 1` | `len >= 16384` |
| 7 | 其余（2..6 线程） | `len >= 65536` |

第 5 条容易读反：它不是「长度阈值变严格」，而是**线程数够多就完全不看长度**，
因为此时省下的拷贝开销已经盖过引用计数的维护成本。

## 断言设计

- 133 条断言全部实跑通过（文件事件 54 + 时间事件 31 + 多线程 I/O 48），每条都**成对构造**：只差一个开关/一个参数，
  例如「有 `BARRIER` / 无 `BARRIER`」「`len=16383` / `len=16384`」「`threads=6` / `threads=7`」。
- 时间事件重排用「回调内推进 50ms」与「回调内不推进」两个用例对拍，确认基准取的是
  **回调返回后的 `now`**（相差正好 50 000 µs）。
- `refcount` 用「`timeProc` 内递归调用 `processTimeEvents`」构造，验证递归期间不释放、
  `finalizer` 不在递归内被调用。
- 随机性在本 demo 中不存在：时钟手动推进、epoll 用注入的就绪列表。

## 注意事项 / 口径

- 本 demo 只覆盖**事件循环框架**与**多线程 I/O 的常量与判定函数**。真实的 `epoll` 封装、
  `beforeSleep` 里的具体工作（fsync、回复刷盘、` clientsCron`）不在范围内。
- Redis 8 的 I/O 线程模型已演进为「每个 I/O 线程自带 `aeEventLoop`」（见 `server.h` 的
  `IOThread.el` / `pending_clients_notifier`），本 demo **没有**建模这部分，只取了稳定可用的常量与判定。
- Go 转写里 `func` 值不可比较（只能与 `nil` 比），因此把回调包成 `*FileProc`，
  用**指针相等**替代 C 的 `fe->wfileProc != fe->rfileProc`。
- `usUntilEarliestTimer` 的「全删返回 -1」分支与 `maxId` 检查，均按抓取到的
  `redis/redis@unstable` 源码实现；不同版本行号/细节可能不同。
- 本机无 Go 工具链，Go 侧以 `bracket_check` / `go_sanity` / `go_crossref` / `syntax_sanity` 静态通过为准。

## 参考资料

- https://github.com/redis/redis/blob/unstable/src/ae.h
- https://github.com/redis/redis/blob/unstable/src/ae.c
- https://github.com/redis/redis/blob/unstable/src/server.h
- https://github.com/redis/redis/blob/unstable/src/object.h
- https://github.com/redis/redis/blob/unstable/src/networking.c
- https://github.com/redis/redis/blob/unstable/src/config.c
- https://github.com/redis/redis/blob/unstable/redis.conf
