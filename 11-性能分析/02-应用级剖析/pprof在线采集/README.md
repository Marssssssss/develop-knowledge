# net/http/pprof 在线采集最小示例

## 简介

- `net/http/pprof` 是 Go 标准库的**在线剖析端点**：`import _ "net/http/pprof"` 一行即可让任何 HTTP 服务在 `/debug/pprof/` 下暴露运行时剖析数据，无需重启、无需改业务代码。
- 核心价值：把"剖析采集"从构建期（`go test -cpuprofile`）和批处理期（`runtime/pprof`）延伸到**长期运行的在线服务**——`go tool pprof http://localhost:6060/debug/pprof/profile` 直接对活进程采样。
- 关键概念：
  - **DefaultServeMux 注册**：包 init() 注册 5 条路径，`http.ListenAndServe("localhost:6060", nil)` 的 nil 正是默认 mux
  - **Index 路由**：`/debug/pprof/<name>` → `pprof.Lookup(<name>)` 命名的运行时 profile；`/debug/pprof/` 列索引页
  - **delta profile**：`?seconds=N` 让快照类 profile 变成"两次采样做差"的增量
  - **流式 vs 快照**：CPU profile 在采集期间边采边写（stream），其余 profile 是即时快照

## 原理详解

路由与参数语义（对照 golang/go `src/net/http/pprof/pprof.go` master，本轮实读源码）：

1. **init() 注册**（Go 1.22 起 pattern 带 `GET ` 前缀，非 GET 请求由 ServeMux 判 405）：
   `"/debug/pprof/"→Index`、`cmdline`、`profile`、`symbol`、`trace` 五条。后四条是**更具体的 pattern**，直接命中各自 handler、不经 Index——若经 Index 委托 `handler("cmdline")` 会在 `pprof.Lookup` 失败后返回 404 "Unknown profile"。
2. **Index**：`strings.CutPrefix(path, "/debug/pprof/")`，name 非空 → 委托 `handler(name)`；name 为空 → HTML 索引页，内容 = `runtime/pprof.Profiles()`（各 profile 附实时 Count）+ 包内 4 个特殊端点，按名排序并列出描述。
3. **handler(name)** 依次判定：
   - `pprof.Lookup(name) == nil` → **404 "Unknown profile"**
   - 有 `seconds` 参数 → **delta 路径**：`ParseInt` 解析，非正整数 → **400**；name 不在 `profileSupportsDelta`（allocs/block/goroutine/goroutineleak/heap/mutex/threadcreate）→ **400 "not supported"**；`seconds` 与 `debug` 并用 → **400 incompatible**；否则 `collectProfile(p0)` → 定时 N 秒 → `collectProfile(p1)` → `p0.Scale(-1)` → `profile.Merge([p0,p1])`，响应文件名 `<name>-delta`
   - `gc` 参数：**仅当 `name=="heap" && gc>0`** 才先 `runtime.GC()`（源码原文 `if name == "heap" && gc > 0`，对其他 profile 静默忽略）
   - `debug != 0` → `text/plain` 明文（legacy text，含函数名/行号注释）；否则 `application/octet-stream` + `Content-Disposition: attachment; filename="<name>"`
4. **Profile（CPU）**：`seconds` 用 `ParseInt`（基 10），`sec <= 0` 或解析错 → **默认 30 秒**；`pprof.StartCPUProfile(w)` 失败（已在采集）→ **500 "Could not enable CPU profiling"**；成功则**边采边流式写出**，睡 N 秒后 `StopCPUProfile()`（同步等待所有写完成）。CPU 之所以不是 `Profile` 对象，正因为它在采集期间持续输出。
5. **Trace**：`seconds` 用 `ParseFloat`（**可带小数**），默认 1 秒；`trace.Start(w)` 流式输出执行踪迹。
6. **Cmdline**：`os.Args` 用 **NUL（`\x00`）** 连接成单行文本。
7. **Symbol**：恒先输出 `num_symbols: 1`（pprof 只关心是否为 0）；PC 列表来自 POST body 或 GET raw query，以 `+` 分隔；`ParseUint(word, 0, 64)` base-0 自动识别 `0x` 前缀；每行输出 `0x... 函数名`。
8. **预设 profile 一览**（runtime/pprof 文档原文口径）：`goroutine`（所有 goroutine 栈）/ `heap`（存活对象分配采样，默认 inuse_space）/ `allocs`（含已 GC 的历史分配，默认 alloc_space）/ `threadcreate` / `block`（阻塞于同步原语的时长，需 `SetBlockProfileRate`）/ `mutex`（争用锁的持有者，栈定位在**临界区结束点**即 Unlock，需 `SetMutexProfileFraction`）。
9. **heap 快照口径**：报告的是**最近一次 GC 完成时点**的统计（刻意略去更新的分配以免偏向垃圾数据）；从未 GC 过则报告全部已知分配。`debug=2` 的 goroutine 输出 = 程序 panic 时的全栈格式。

```
GET /debug/pprof/heap?gc=1&debug=1
     │
     ├─ mux："/debug/pprof/cmdline|profile|symbol|trace" 精确命中（不经 Index）
     └─ Index：CutPrefix → name="heap" → handler("heap")
              ├─ Lookup("heap") ✓
              ├─ seconds? ── 有 → delta: p0 → sleep → p1 → Scale(-1) → Merge
              ├─ heap && gc>0 → runtime.GC()
              └─ debug!=0 → text/plain；否则 octet-stream + attachment
```

## 对比 / 选型

| 方式 | 适用 | 特点 |
| --- | --- | --- |
| `go test -cpuprofile/-memprofile` | 基准测试 | 零代码改动，进程结束产出文件 |
| `runtime/pprof` 显式 API | 一次性/批处理程序 | 需在 main 里加 Start/Stop 代码 |
| `net/http/pprof` | 长期运行的服务 | 在线拉取、可带参数、无需重启 |

## 环境准备

- 操作系统：任意（本 demo 为纯逻辑模拟，不监听端口）
- Python ≥ 3.10 / Go ≥ 1.21（Go 版本机无工具链，走人工审查 + 括号配平）

## 运行方式

```bash
python3 python/pprof_http.py   # 15 组断言
# go run go/pprof_http.go      # 同 15 组断言
```

## 关键代码片段

```python
def serve_handler(rt, name, query):
    if rt.lookup(name) is None:
        return Resp(404, "Unknown profile\n")          # Lookup 失败 → 404
    sec = query.get("seconds", "")
    if sec != "":                                       # delta 路径优先于 gc/debug
        n = parse_int(sec)
        if n is None or n <= 0:
            return Resp(400, 'invalid value for "seconds" ...')
        if name not in DELTA_OK:
            return Resp(400, '"seconds" parameter is not supported ...')
        ...
    gc = parse_int(query.get("gc", "0") or "0") or 0
    if name == "heap" and gc > 0:                       # 仅 heap 触发 GC
        rt.gc()
```

## 性能与边界

- CPU 采样默认约 **100 次/秒**（Go 官方博客口径），2525 个样本 ≈ 25 秒程序。
- 内存剖析按 **1/524288（约每 512KB）采样** 记录分配，数字是近似值（官方博客原文 "1-in-524288 sampling rate"）。
- delta profile 会让请求挂起 N 秒，服务端会按需放宽 WriteTimeout（源码 `configureWriteDeadline`）。

## 注意事项与常见坑

- **`seconds` 只对快照类 profile 是"做差"语义，对 `profile`/`trace` 是"采集时长"语义**——同一个参数名两种含义，混用 400。
- **`gc=1` 对非 heap profile 无效但不报错**：源码显式 `name == "heap" && gc > 0`，别以为给 allocs 加 gc 会触发。
- **`/debug/pprof/profile` 默认 30 秒**：压测/抓包超时时间要按 30s+ 留，或显式传 `seconds`。
- **四条特殊路径不经 Index**：自己拼 mux 或做网关重写时保持这 5 条路径的精确匹配语义，否则 `handler("cmdline")` 之类的委托会 404。
- **安全**：端点暴露全部栈信息，只应绑定 localhost 或加鉴权（官方示例 `http.ListenAndServe("localhost:6060", nil)`）。
- 生产环境：`block`/`mutex` 需要程序里先调 `runtime.SetBlockProfileRate` / `SetMutexProfileFraction`，默认关闭。

## 参考资料（实际阅读过的权威来源）

- [net/http/pprof - Go Packages](https://pkg.go.dev/net/http/pprof) — 端点清单、Parameters 一节（debug/gc/seconds）、各 handler 语义
- [golang/go src/net/http/pprof/pprof.go（master 源码）](https://raw.githubusercontent.com/golang/go/master/src/net/http/pprof/pprof.go) — init 注册、Index 委托、delta 实现、错误码与 profileSupportsDelta 全量
- [runtime/pprof - Go Packages](https://pkg.go.dev/runtime/pprof) — 预设 profile 定义、heap 快照口径、mutex 栈定位在 Unlock
- [Profiling Go Programs - Go Blog（Russ Cox）](https://go.dev/blog/pprof) — 100Hz 采样、1/524288 内存采样、在线采集用法
