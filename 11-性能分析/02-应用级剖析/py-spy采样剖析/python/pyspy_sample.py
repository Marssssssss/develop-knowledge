# -*- coding: utf-8 -*-
"""py-spy 采样剖析复刻：dump/top/record 三模式、GIL 过滤、nonblocking 撕裂读、attach 权限。

口径（py-spy 官方 README，benfred/py-spy，本轮实读）：
  - py-spy 是采样剖析器：不重启、不修改目标程序；自身是 Rust 写的独立进程，
    通过 process_vm_readv(Linux)/vm_read(macOS)/ReadProcessMemory(Windows)
    **直接读目标进程内存**
  - 栈重建：从全局 PyInterpreterState 拿到全部 Python 线程 → 逐线程迭代
    PyFrameObject（back 链）得到调用栈；ABI 随版本变化 → bindgen 生成各版本结构
  - ASLR：有符号时 deref interp_head/_PyRuntime；符号被剥离时扫描 BSS 段找
    "看起来指向合法 PyInterpreterState" 的地址
  - top：类 Unix top 的实时视图（%own/%total/%GIL）；dump：一次性打印各线程当前栈
    （定位挂起程序），--locals 可带局部变量；record：火焰图/speedscope
  - --gil：只采样持有 GIL 的线程；注意会**漏掉释放了 GIL 仍在干活的原生扩展**
  - GIL 归属判定：Python ≤3.6 用 _PyThreadState_Current，≥3.7 从 _PyRuntime 结构推导
  - --nonblocking：不暂停目标进程；读非原子 → 偶发采样错误/残缺栈
  - 权限：Linux attach 非子进程通常要 root（ptrace_scope）；macOS 恒需 root；
    Docker 需 --cap-add SYS_PTRACE；K8s 需 securityContext 加 SYS_PTRACE
"""
import platform


class Frame:
    def __init__(self, func, back=None, locs=None):
        self.func = func
        self.back = back       # 指向调用者的 PyFrameObject（f_back）
        self.locals = locs or {}


class Thread:
    def __init__(self, tid, name):
        self.tid, self.name = tid, name
        self.current = None    # 最内层（执行中）PyFrameObject
        self.holds_gil = False


class Interp:
    """PyInterpreterState：全部线程。"""

    def __init__(self, threads):
        self.threads = threads


class Process:
    def __init__(self, pid, argv, interp, pyver):
        self.pid, self.argv, self.interp, self.pyver = pid, argv, interp, pyver


class SampleError(Exception):
    pass


READ_CALL = {"Linux": "process_vm_readv", "Darwin": "vm_read", "Windows": "ReadProcessMemory"}


def read_call_for(system):
    return READ_CALL[system]


def find_interp_address(has_symbols):
    """有符号：deref interp_head/_PyRuntime；无符号（被剥离）：扫描 BSS 段。"""
    if has_symbols:
        return "deref:interp_head_or__PyRuntime"
    return "scan:bss_section"


def gil_owner_method(pyver):
    """GIL 归属判定入口：≤3.6 → _PyThreadState_Current；≥3.7 → _PyRuntime 内部推导。"""
    major, minor = pyver
    return "_PyThreadState_Current" if (major, minor) <= (3, 6) else "_PyRuntime"


class Spy:
    """独立的采样器进程：只读目标内存，不注入任何代码。"""

    def __init__(self, nonblocking=False):
        self.nonblocking = nonblocking
        self.sample_errors = 0

    def read_stack(self, proc, thread, torn=False):
        """遍历 PyFrameObject 的 back 链：执行中帧 → … → 根帧。
        nonblocking 且发生撕裂读：只读到一半（残缺栈）或抛 SampleError。"""
        stack, f = [], thread.current
        while f is not None:
            stack.append(f)
            if torn and len(stack) == 1:   # 撕裂：第一个 back 指针读到旧值 → 截断
                break
            f = f.back
        stack.reverse()                     # 根在前
        if torn and not stack:
            self.sample_errors += 1
            raise SampleError("torn read")
        return stack

    def dump(self, proc, show_locals=False):
        """py-spy dump：一次性打印各线程当前栈 + 进程信息。"""
        lines = ["Process %d: %s" % (proc.pid, " ".join(proc.argv))]
        lines.append("Python v%d.%d.%s" % (proc.pyver[0], proc.pyver[1], "x"))
        for t in proc.interp.threads:
            lines.append('Thread %d "%s" (native): %s' %
                         (t.tid, t.name, "idle" if t.current is None else "active"))
            for f in self.read_stack(proc, t):
                loc = ""
                if show_locals and f.locals:
                    loc = " " + ", ".join("%s=%s" % kv for kv in sorted(f.locals.items()))
                lines.append("    %s%s" % (f.func, loc))
        return "\n".join(lines)

    def top(self, proc, ticks, gil_only=False):
        """py-spy top：实时聚合 %own（叶子帧=自身）与 %total（在栈上）、%GIL。"""
        own, total, gil = {}, {}, {}
        for _ in range(ticks):
            for t in proc.interp.threads:
                if gil_only and not t.holds_gil:
                    continue
                if t.current is None:
                    continue
                stack = self.read_stack(proc, t)     # top 默认阻塞式（暂停目标读一致性快照）
                total_samples = len(stack)
                for f in stack:
                    total[f.func] = total.get(f.func, 0) + 1
                own[stack[-1].func] = own.get(stack[-1].func, 0) + 1
                if t.holds_gil:
                    gil[t.name] = gil.get(t.name, 0) + 1
        return {"own": own, "total": total, "gil": gil, "ticks": ticks}

    def record(self, proc, ticks):
        """py-spy record：折叠栈（collapsed stacks）——火焰图的数据形态。"""
        collapsed = {}
        for _ in range(ticks):
            for t in proc.interp.threads:
                if t.current is None:
                    continue
                stack = self.read_stack(proc, t)
                key = ";".join(f.func for f in stack)
                collapsed[key] = collapsed.get(key, 0) + 1
        return collapsed


def attach_allowed(system, is_child, is_root, ptrace_scope=1):
    """attach 权限模型（README FAQ 口径）：
    Linux：默认 ptrace_scope=1 时 attach 非子进程需 root；macOS：恒需 root。"""
    if system == "Darwin":
        return is_root
    if system == "Linux":
        if ptrace_scope == 0:
            return True
        return is_child or is_root
    return True   # Windows：同用户即可


def docker_ok(cap_sys_ptrace):
    """Docker 默认禁止 process_vm_readv，需 --cap-add SYS_PTRACE。"""
    return cap_sys_ptrace


def make_demo_process():
    """两线程演示进程：worker 在 work/parse 间切换；native 线程释放 GIL 跑 C 扩展。"""
    root = Frame("main.main", locs={"url": "'/api'"})
    work = Frame("app.work", back=root, locs={"n": "42"})
    leaf = Frame("app.parse", back=work)
    worker = Thread(101, "MainThread")
    worker.current, worker.holds_gil = leaf, True

    cframe = Frame("ext.native_loop")
    native = Thread(102, "CythonThread")
    native.current, native.holds_gil = cframe, False   # 释放 GIL 的原生扩展

    return Process(1234, ["python", "app.py"], Interp([worker, native]), (3, 11))


def main():
    proc = make_demo_process()
    spy = Spy()

    # 1. 栈重建：back 链 → 根在前
    t = proc.interp.threads[0]
    stack = spy.read_stack(proc, t)
    assert [f.func for f in stack] == ["main.main", "app.work", "app.parse"]

    # 2. dump：进程信息 + 各线程栈；--locals 带局部变量
    text = spy.dump(proc)
    assert "Process 1234" in text and "Thread 101" in text and "Thread 102" in text
    assert "app.parse" in text and "ext.native_loop" in text
    text = spy.dump(proc, show_locals=True)
    assert "url='/api'" in text and "n=42" in text
    assert "main.main url='/api'" in text, "locals 紧跟所属帧"

    # 3. top：own=叶子、total=在栈上
    r = spy.top(proc, ticks=10)
    assert r["own"] == {"app.parse": 10, "ext.native_loop": 10}
    assert r["total"]["main.main"] == 10 and r["total"]["app.work"] == 10
    assert r["total"]["app.parse"] == 10 and r["total"]["ext.native_loop"] == 10

    # 4. %GIL：MainThread 每个采样期都持锁 → 10/10
    assert r["gil"] == {"MainThread": 10}

    # 5. --gil 过滤：原生线程（释放 GIL）被排除 —— 注意这会漏掉仍在干活的原生扩展
    r = spy.top(proc, ticks=5, gil_only=True)
    assert "ext.native_loop" not in r["own"] and "app.parse" in r["own"]
    assert "ext.native_loop" not in r["total"]

    # 6. --nonblocking：撕裂读产生残缺栈（叶子侧保留、根侧丢失）并计错误
    nb = Spy(nonblocking=True)
    st = nb.read_stack(proc, t, torn=True)
    assert [f.func for f in st] == ["app.parse"], "撕裂只读到最内层帧"
    idle = Thread(103, "Idle")
    idle.current = None
    proc2 = Process(1, ["x"], Interp([idle]), (3, 11))
    try:
        nb.read_stack(proc2, idle, torn=True)
        raise AssertionError("空栈应抛 SampleError")
    except SampleError:
        pass
    assert nb.sample_errors == 1

    # 7. record：折叠栈形态（火焰图输入）
    c = spy.record(proc, ticks=4)
    assert c == {"main.main;app.work;app.parse": 4, "ext.native_loop": 4}

    # 8. 读内存系统调用映射
    assert read_call_for("Linux") == "process_vm_readv"
    assert read_call_for("Darwin") == "vm_read"
    assert read_call_for("Windows") == "ReadProcessMemory"

    # 9. 解释器地址发现：有符号 vs 无符号（BSS 扫描）
    assert find_interp_address(True).startswith("deref")
    assert find_interp_address(False).startswith("scan")

    # 10. GIL 判定入口随版本切换
    assert gil_owner_method((3, 6)) == "_PyThreadState_Current"
    assert gil_owner_method((3, 7)) == "_PyRuntime"
    assert gil_owner_method((3, 11)) == "_PyRuntime"

    # 11. attach 权限：Linux 非子进程默认需 root；自建进程不需要；macOS 恒需 root
    assert not attach_allowed("Linux", is_child=False, is_root=False)
    assert attach_allowed("Linux", is_child=True, is_root=False)
    assert attach_allowed("Linux", is_child=False, is_root=True)
    assert attach_allowed("Linux", is_child=False, is_root=False, ptrace_scope=0)
    assert not attach_allowed("Darwin", is_child=True, is_root=False)
    assert attach_allowed("Darwin", is_child=True, is_root=True)

    # 12. Docker/K8s：SYS_PTRACE 能力位
    assert not docker_ok(False) and docker_ok(True)

    print("pyspy_sample: 12 组断言全部通过")


if __name__ == "__main__":
    assert platform is not None  # 保留 import 供平台口径说明（见 README 权限模型）
    main()
