# py-spy：对运行中 Python 进程的无侵入采样剖析（dump / top）

## 简介

- py-spy 是用 Rust 写的**采样剖析器**：不重启目标程序、不修改任何代码、不注入探针，就能看到任意运行中 Python 程序在干什么——官方明确"safe to use against production Python code"。
- 核心价值：与 cProfile（要在目标进程内跑钩子）相反，py-spy 是**旁观进程**，对被测程序近乎零扰动；`dump` 一次快照定位挂死程序，`top` 实时看热点，`record` 出火焰图。
- 关键概念：
  - **跨进程读内存**：process_vm_readv（Linux）/ vm_read（macOS）/ ReadProcessMemory（Windows）
  - **栈重建**：PyInterpreterState → 全部线程 → 沿 PyFrameObject 的 back 链走
  - **GIL 过滤**（--gil）：只看持锁线程；%GIL 列
  - **nonblocking**（--nonblocking）：不暂停目标，代价是偶发撕裂读

## 原理详解

依据 benfred/py-spy 官方 README（本轮实读）：

1. **为什么"无侵入"成立**：剖析代码不在目标进程里跑，py-spy 只**从另一个进程直接读目标内存**——目标进程的执行路径完全不变（这正是生产环境敢用的理由；README FAQ "Why do we need another Python profiler?"）。
2. **调用栈怎么来的**：先定位全局 `PyInterpreterState`（拿到所有 Python 线程），再对每个线程**迭代其 PyFrameObject 链**得到调用栈。Python ABI 每个版本都变，py-spy 用 Rust bindgen 为每个关心的解释器版本生成对应结构体来匹配内存布局。
3. **对抗 ASLR**：目标解释器带符号时，直接 deref `interp_head`（新版本是 `_PyRuntime`）拿到地址；二进制被剥离符号（Windows 无 PDB）时，**扫描 BSS 段**寻找"看起来指向合法 PyInterpreterState"的候选地址并验证其布局。
4. **三个子命令**：
   - `py-spy dump --pid <pid>`：一次性打印**每个线程**的当前调用栈 + 进程基本信息——"只需要一条栈来搞清程序挂在哪"的场景；`--locals` 连每帧的局部变量一起打。
   - `py-spy top --pid <pid>`：类 Unix top 的实时视图，显示各函数的耗时占比（采样聚合：自身=叶子帧，总耗时=出现在栈上）。
   - `py-spy record -o profile.svg --pid <pid>`：持续采样产出火焰图（也支持 speedscope/raw 格式）。
5. **GIL 检测**：≤3.6 看 `_PyThreadState_Current` 指向的线程；3.7+ 从 `_PyRuntime` 结构推导等价信息。top 视图有 %GIL 列；`--gil` 只保留**持有 GIL 的线程**的采样——注意口径：这会**漏掉已释放 GIL 但仍在计算的原生扩展**（README 原文 "will miss activity in extensions that release the GIL while still active"）。
6. **--nonblocking**：默认每次采样会短暂暂停目标进程保证一致性快照；`--nonblocking` 完全不暂停，但内存读**非原子**且取一条栈要多次读 → 偶发采样错误或**残缺栈**（部分帧）。
7. **--native**：可以剖析 C/C++/Cython 原生扩展（部分平台）；Cython 程序要拿到原始 .pyx 的行号，需要生成出来的 C/C++ 文件在场。
8. **--subprocesses**：自动 attach 目标的子进程（multiprocessing / gunicorn worker 池），火焰图中子进程作为父进程的子节点出现。
9. **权限模型**（README FAQ）：
   - Linux：默认 ptrace_scope 配置下，**attach 非子进程需要 root**；让 py-spy 自己拉起进程（`py-spy record -- python app.py`）则不需要
   - macOS：**始终需要 root**
   - Docker：容器默认禁 process_vm_readv → `--cap-add SYS_PTRACE`；K8s 在 securityContext 里 `capabilities: add: [SYS_PTRACE]`（或 ephemeral container 方案）

```
py-spy 进程 ──read──▶ 目标 Python 进程内存
   │ process_vm_readv / vm_read / ReadProcessMemory
   ├─ 定位 PyInterpreterState（符号 deref │ 无符号扫 BSS）
   ├─ 遍历线程 ──▶ PyFrameObject back 链 ──▶ 调用栈
   └─ dump（单次快照） / top（实时聚合） / record（折叠栈→火焰图）
```

## 对比 / 选型

| 工具 | 方式 | 目标进程扰动 | 需重启/改代码 | 适合 |
| --- | --- | --- | --- | --- |
| py-spy | 采样（旁观进程） | 近零 | 否 | 生产环境、挂死诊断 |
| cProfile | 确定性插桩（进程内） | 中（C 级实现） | 是 | 离线精确计数（ncalls/累计） |
| profile（纯 Py） | 确定性插桩 | 显著 | 是 | 扩展剖析器本身 |

## 环境准备

- 操作系统：Linux / macOS / Windows / FreeBSD（demo 为纯逻辑模拟，不真读内存）
- Python ≥ 3.10 / Go ≥ 1.21（Go 版本机无工具链，走人工审查 + 括号配平）

## 运行方式

```bash
python3 python/pyspy_sample.py   # 12 组断言
# go run go/pyspy_sample.go      # 同 12 组断言
```

## 关键代码片段

```python
def read_stack(self, proc, thread, torn=False):
    """遍历 PyFrameObject 的 back 链：执行中帧 → … → 根帧。"""
    stack, f = [], thread.current
    while f is not None:
        stack.append(f)
        if torn and len(stack) == 1:   # 撕裂读：back 指针读到旧值 → 残缺栈
            break
        f = f.back
    stack.reverse()                     # 根在前
```

## 性能与边界

- 采样率可用参数调整（samples per second）；py-spy 自身 Rust 实现且独立进程，开销"extremely low"（README 原文）。
- 撕裂读的代价是**残缺栈**：非阻塞模式采样错误率上升、出现部分帧——统计结论可用，单条栈要谨慎。
- 不支持 PyPy、不支持 32 位 Windows（README FAQ 明确 "Not yet"）。

## 注意事项与常见坑

- **--gil 会漏原生扩展**：释放 GIL 干活的 C 扩展线程被过滤掉，看到"Python 很闲"可能是 numpy/Cython 在满负荷跑——对 CPU 密集扩展场景别开 --gil（或加 --native）。
- **权限错误不是 bug**：Linux 上 attach 别人的进程要 root；Docker/K8s 报 Permission Denied 几乎都是缺 SYS_PTRACE 能力位。
- **dump 是诊断挂死的利器**：不需要采样窗口，单次快照即可看到卡住的栈；配 --locals 直接看现场变量。
- **采样剖析只能给相对占比**：低频短函数可能整轮采样都采不到，不代表没执行（与确定性插桩的本质差异）。

## 参考资料（实际阅读过的权威来源）

- [benfred/py-spy README（GitHub 官方）](https://github.com/benfred/py-spy) — 三子命令、工作原理（process_vm_readv/PyInterpreterState/bindgen/ASLR）、GIL 检测、--native/--subprocesses/--nonblocking、权限 FAQ（sudo/Docker/K8s）
- [The Python Profilers - Python 官方文档](https://docs.python.org/3/library/profile.html) — 采样 vs 确定性剖析的定义对照（"statistical profiling randomly samples the effective instruction pointer"）
