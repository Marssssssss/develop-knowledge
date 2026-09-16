# Stream 背压机制（write/drain/highWaterMark/pipe）

## 简介

- **背压（backpressure）**：下游消费慢于上游生产时，数据在内存里堆积的问题。Node.js Streams 用 `write()` 返回值 + `'drain'` 事件 + `highWaterMark` 构成一套显式流控协议，`pipe()` 则自动执行这套协议。
- 官方语义（stream 文档 `writable.write()`）："The return value is `true` if the internal buffer is less than the `highWaterMark` configured when the stream was created **after admitting chunk**. If `false` is returned, further attempts to write data to the stream should stop until the `'drain'` event is emitted. While a stream is not draining, calls to `write()` will buffer `chunk`, and return `false`."
- `highWaterMark` 默认 **16384 字节**（对象模式 **16 个对象**，本机 v22.22.2 实测）；官方强调它是**阈值不是限制**："The `highWaterMark` option is a threshold, not a limit: it dictates the amount of data that a stream buffers before it stops asking for more data. It does not enforce a strict memory limitation in general."

## 原理详解

### 手动背压协议（官方推荐的 'drain' 写法）

stream 文档原文（"it is possible to respect backpressure and avoid memory issues using the `'drain'` event"）：

```js
function write(data, cb) {
  if (!stream.write(data)) stream.once('drain', cb);
  else process.nextTick(cb);
}
// Wait for cb to be called before doing any other write.
```

`writableLength` 是队列内待写字节数/对象数的内省窗口；`writableNeedDrain`（v15.2.0+）标记"缓冲已满、将发 `'drain'`"。

### pipe() 的自动背压

官方原文："The `readable.pipe()` method attaches a Writable stream to the readable, causing it to switch automatically into flowing mode and push all of its data to the attached Writable. **The flow of data will be automatically managed so that the destination Writable stream is not overwhelmed by a faster Readable stream.**" 实现方式即：`write()` 返回 `false` 时对 readable 调 `pause()`，`'drain'` 时恢复——本 demo 用 `'pause'` 事件计数观测到 100 条数据 / hwm=4 时 pipe 暂停 readable **25 次**（每轮 4 条）。

### Readable 的两模式与三状态

- 两模式（Two reading modes）：**flowing**（系统自动推送，经 `'data'` 事件）与 **paused**（需显式 `read()`）；所有 Readable 初始为 paused，加 `'data'` 监听、调 `pipe()`/`resume()` 切到 flowing。
- 三状态（Three states）：`readableFlowing` ∈ `null` / `false` / `true`。反直觉行为（官方原文）："Calling `readable.pause()`, `readable.unpipe()`, **or receiving backpressure** will cause the `readableFlowing` to be set as `false` … While in this state, attaching a listener for the `'data'` event **will not** switch `readableFlowing` to `true`."——本 demo E3d 实测：`unpipe()` 后补挂 `'data'` 监听，数据事件**不触发**。

### 缓冲计数口径（Buffering 一节）

- 普通流按**字节**计；对象模式按**对象个数**计；操作（不解码）字符串的流按 **UTF-16 码元**计。本 demo E5：hwm=3（对象模式）连写 3 个各 1KB 的对象，第 3 个就返回 `false`——对象的大小无关。

## 对比

| 写法 | 缓冲峰值(本机实测, 200 条/hwm=4) | 说明 |
| --- | --- | --- |
| 无视 `write()` 返回值 | **200**（全量驻留内存） | 数据不丢但内存无界增长——大文件/高流量下 OOM 根因 |
| 尊重背压（等 `'drain'`） | **3**（≤ hwm） | 吞吐略降，内存有界 |
| `pipe()` / `pipeline()` | 有界（内部自动执行上述协议） | 生产代码首选；`pipeline()` 额外带错误传播与清理 |

## 环境

- Node.js ≥ 14（本 demo 实测 v22.22.2）；无第三方依赖
- TS 版用 Node 22 内置 `--experimental-strip-types` 直接运行

## 运行方式

```bash
cd js && node main.js                              # 约 1 秒,14 项断言
cd ts && node --experimental-strip-types main.ts   # TS 版同组断言
```

## 关键代码

- `js/main.js`：五个实验——E1 write/drain 返回值语义；E2 无背压 vs 有背压的 `writableLength` 峰值对比；E3 pipe 自动背压（`'pause'` 事件计数）+ unpipe 后 `'data'` 不恢复流动；E4 hwm 阈值非限制（缓冲 24B > hwm 8B）；E5 对象模式按个数计数。

## 性能边界

- 尊重背压会以吞吐换内存：每次 `'drain'` 往返引入一次事件循环调度；高吞吐低延迟场景可适当调大 hwm（代价是内存峰值）。
- 本 demo E2a 的"无背压峰值 200"是对象模式小对象；字节模式下无视背压的峰值 = 生产者速度差 × 时间，GB 级流可轻松堆出数百 MB。

## 注意事项与常见坑

- **`write()` 返回 `false` 不是错误**：数据已进缓冲，只是"请停下等 `'drain'`"；继续写不丢数据但内存无界（E4 证明缓冲可超 hwm）。
- **`unpipe()` 后补挂 `'data'` 监听不会恢复流动**（E3d 实测）——这是排查"pipe 拆掉后流卡死"的关键：需要显式 `resume()`。
- **E3 断言改用 `'pause'` 事件的教训**：interval 采样 `isPaused()` 是时序运气（流动窗口在单个宏任务内即完成切换，1ms 采样器经常全程只见暂停态），观测流状态要用事件而非采样。
- **对象模式 hwm 与数据大小无关**：传大对象时 16 个对象可能已是几十 MB——按需自定义 `highWaterMark`。
- `readableFlowing === null` 是"从未接消费者"，`false` 是"接过后暂停/被背压"，两者语义不同。

## 参考资料

- Node.js 官方文档 — Stream API（`writable.write()` 返回值 / `Event: 'drain'` / `writableLength` / `Buffering` / `Two reading modes` / `Three states` / `readable.pipe()`）：<https://nodejs.org/api/stream.html>（本 demo 开发时经 curl 下载该页并逐节提取原文核对）
