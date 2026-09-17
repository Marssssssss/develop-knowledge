# Frida Interceptor：Native 函数 hook 原理

> `Interceptor.attach` 在目标函数入口写跳转实现 **inline hook**：调用先进入 JS 的 `onEnter/onLeave`，再回原函数。配套 `native_hook_agent.js`（真实 API 风格）与 `interceptor_sim.py`（trampoline/编组/flush 的可执行模型，10 项断言实跑）。

## 简介

Frida 的 `Interceptor` 是 GumJS 对 native 代码的通用拦截层：给定一个 `NativePointer`（函数地址），它在函数入口 patch 一条跳转，把调用重定向到 JS 回调；原指令被搬进 **trampoline**，因此替换函数/`onLeave` 里仍能调原始实现。`Module.getExportByName('libc.so', 'open')` 负责把符号解析成地址。官方文档同时给出 `replace`（整函数替换）与 `attach`（观测+改参/改返回值）两种姿态。

## 原理详解

1. **`Interceptor.attach(target, callbacks)`**：`target` 是 `NativePointer`。`onEnter(args)` 中 `args` 是可读写的 `NativePointer` 数组（按调用约定从寄存器/栈上取出）；`onLeave(retval)` 中 `retval.replace(1337)` 可改返回值。**回调对象是循环复用的，勿存出回调外**。
2. **回调里 `this` 的上下文**：`returnAddress` / `context`（`pc`、`sp` 及 `eax/rax/r0/x0` 等寄存器，可写） / `threadId` / `depth` / `errno`。
3. **Thumb 位细节**：32 位 ARM 上，ARM 函数地址 LSB=0、Thumb 函数 LSB=1；用 Frida API 拿到的地址已处理好。
4. **`Interceptor.replace(target, replacement)`**：用 `NativeCallback` 实现完整替换；想在替换里链回原实现，须先把原地址包成 `NativeFunction` 再同步调用（**绕过 hook 直达原实现**）。`replaceFast` 开销更低但需要用返回的指针调原实现；`revert(target)` 撤销；`detachAll()` 全撤；`flush()` 提交未生效的 patch（离开 JS 运行时或调用 `send()` 时自动 flush）。
5. **热路径优化**：`onEnter/onLeave` 可以直接是 CModule 编译出的 C 函数指针（`void onEnter(GumInvocationContext *)`），绕开 JS 编组开销。官方基准：iPhone 5S 上只给 `onEnter` ≈ 6 µs，两个都给 ≈ 11 µs——**只挂需要的回调**。
6. **模块解析**：`Module.findGlobalExportByName` 全局搜符号代价高应少用；`Module` 对象有 `name/base/size/path` 属性（新版已无 `getBaseAddress()`，直接用 `.base`）。

## 对比

| 手段 | 层级 | 改内存 | 典型用途 |
| --- | --- | --- | --- |
| `Interceptor.attach` | 指令级 inline hook | 函数入口 patch | 观测参数/返回值、hexdump 缓冲区 |
| `Interceptor.replace` | 函数级替换 | 入口跳 `NativeCallback` | 整体改写语义(如禁用检测函数) |
| Java 层 `implementation` | ART 方法结构 | ArtMethod 入口 | Java 方法(见 Frida-Java层hook demo) |

## 环境

- `python`（≥3.8，标准库）：跑模拟断言
- 真机：root + frida-server；桌面 `pip install frida-tools`

## 运行方式

```bash
python interceptor_sim.py      # 10 项断言
frida-trace -U -i open -N com.android.chrome   # 官方 Chrome open() 追踪示例
```

## 关键代码

- `NativeFunc.attach`：把入口字节换成 `jmp hook_stub`，原 prologue 搬进 trampoline——patch 先入 `pending` 队列，`flush()`/`send()` 才生效（对应官方 flush 语义）。
- `hook_stub`：保存参数 → 跑 `onEnter` → 执行「搬走的 prologue + 原函数体」→ `onLeave(retval)`（含 `retval.replace`）。
- `CModule` 快通道：回调为纯 C 语义时跳过 JS 编组，模拟中以 `overhead` 数值体现（6/11 µs 口径）。

## 性能边界

- 官方口径：iPhone 5S 基线 onEnter-only ≈ 6 µs、onEnter+onLeave ≈ 11 µs；`send()` 单条消息未针对高频优化，需自行权衡延迟/吞吐（批量合并）。
- inline hook 长度受函数序言指令边界约束（真实实现按指令重定位，模拟只搬定长字节，不模拟指令解码——口径已在代码注释声明）。

## 注意事项与常见坑

- **回调对象跨调用复用**：把 `retval`/`args` 元素存到回调外再用，读到的是别人的调用。
- **`replace` 里直接调原地址会递归进 hook**，必须用替换前包好的 `NativeFunction`（本 demo 断言 7 专门验证这一点）。
- **忘了 flush**：patch 在 `send()`/离开运行时前可能未生效，调用了却看不到 hook 是常见症状（断言 5/6）。
- 只需要观测参数时**别挂 onLeave**，反之亦然——白付 5 µs。
- 模拟未实现真实指令重定位/寄存器保存区布局，语义模型以官方文档为准。

## 参考资料（实际读过）

- [Frida JavaScript API — Interceptor / Module](https://frida.re/docs/javascript-api/)
- [Frida 官方文档 — Android（frida-trace 示例）](https://frida.re/docs/android/)
