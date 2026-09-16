"""fork 与僵尸进程:跨平台语义模拟(可在 Windows 自检)。

Windows 无 os.fork,本文件用纯数据结构模拟 wait(2) 描述的内核语义,
并实现与之位位对应的 wstatus 解码宏;所有结论均以断言验证。

模拟口径(与 README"参考资料"中的 man 页逐条对应):
- 僵尸 = 子进程已终止但父进程尚未 wait;占用进程表槽位
- SIGCHLD=SIG_IGN / SA_NOCLDWAIT → 子进程终止不变僵尸,wait → ECHILD
- 父进程先终止 → 僵尸被 init(1)(或 subreaper)收养并自动收尸
- WEXITSTATUS 取退出参数低 8 位;WNOHANG 无状态变化时返回 0
"""

# ---------- wstatus 位编码(Linux <sys/wait.h> 宏展开口径) ----------

def wifexited(w: int) -> bool:
    return (w & 0x7f) == 0


def wexitstatus(w: int) -> int:
    return (w >> 8) & 0xff


def wifsignaled(w: int) -> bool:
    sig = w & 0x7f
    return 0 < sig < 0x7f


def wtermsig(w: int) -> int:
    return w & 0x7f


def encode_exit(code: int) -> int:
    """子进程 _exit(code) 后内核构造的 wstatus:退出参数只保留低 8 位。"""
    return (code & 0xff) << 8


def encode_signal(sig: int) -> int:
    """子进程被信号杀死:wait(2) 口径为"信号致死时 wstatus 即信号编号"。"""
    return sig & 0x7f


# ---------- 内核语义模拟 ----------

class ChildProcessError(Exception):
    """对应 errno=ECHILD:没有可 wait 的子进程。"""


class FakeKernel:
    """模拟进程表:RUNNING -> ZOMBIE -> REAPED 的状态机。"""

    RUNNING, ZOMBIE, REAPED = "R", "Z", "reaped"

    def __init__(self):
        self.procs = {}          # pid -> dict(state, wstatus)
        self.next_pid = 100
        self.sigchld_ign = False  # SIGCHLD 是否被设为 SIG_IGN

    def fork(self) -> int:
        pid = self.next_pid
        self.next_pid += 1
        self.procs[pid] = {"state": self.RUNNING, "wstatus": None}
        return pid

    def child_exit(self, pid: int, code: int) -> None:
        self._terminate(pid, encode_exit(code))

    def child_signal_death(self, pid: int, sig: int) -> None:
        self._terminate(pid, encode_signal(sig))

    def _terminate(self, pid: int, wstatus: int) -> None:
        p = self._get(pid)
        assert p["state"] == self.RUNNING, "double terminate"
        if self.sigchld_ign:
            # SIG_IGN / SA_NOCLDWAIT:不产生僵尸,直接回收
            p["state"], p["wstatus"] = self.REAPED, wstatus
        else:
            p["state"], p["wstatus"] = self.ZOMBIE, wstatus

    def waitpid(self, pid: int, nohang: bool = False):
        """三态:pid(成功收尸)/ 0(WNOHANG 且无状态变化)/ 抛 ECHILD。"""
        p = self._get(pid)
        if p["state"] == self.ZOMBIE:
            p["state"] = self.REAPED          # 一次 wait 即清除僵尸
            return pid, p["wstatus"]
        if p["state"] == self.REAPED:
            raise ChildProcessError("ECHILD: child already reaped")
        # RUNNING:
        if nohang:
            return 0, 0
        raise RuntimeError("would block (simulation requires child first exit)")

    def parent_exits(self, ppid: int) -> list:
        """父进程终止:其僵尸子进程被 init(1)/subreaper 收养并自动收尸。"""
        adopted = []
        for pid, p in self.procs.items():
            if pid != ppid and p["state"] == self.ZOMBIE:
                p["state"] = self.REAPED
                adopted.append(pid)
        return adopted

    def _get(self, pid):
        p = self.procs.get(pid)
        if p is None:
            raise ChildProcessError("ECHILD: no such child")
        return p


# ---------- 场景断言 ----------

def test_status_macros():
    # 正常退出 _exit(42)
    w = encode_exit(42)
    assert wifexited(w) and wexitstatus(w) == 42
    assert not wifsignaled(w)
    # 低 8 位截断:300 = 0x12C → 0x2C = 44;-1 → 0xFF = 255
    assert wexitstatus(encode_exit(300)) == 44
    assert wexitstatus(encode_exit(-1)) == 255
    assert wexitstatus(encode_exit(256)) == 0
    # 信号致死
    w = encode_signal(9)  # SIGKILL
    assert wifsignaled(w) and wtermsig(w) == 9
    assert not wifexited(w)
    print("PASS: wstatus encoding macros (low-8-bit truncation, signal death)")


def test_zombie_lifecycle():
    k = FakeKernel()
    pid = k.fork()
    r, _ = k.waitpid(pid, nohang=True)
    assert r == 0, "WNOHANG must return 0 while child running"

    k.child_exit(pid, 42)
    p = k.procs[pid]
    assert p["state"] == FakeKernel.ZOMBIE, "dead-unwaited child must be zombie"

    r, w = k.waitpid(pid, nohang=True)
    assert r == pid and wifexited(w) and wexitstatus(w) == 42
    assert k.procs[pid]["state"] == FakeKernel.REAPED, "wait clears zombie"

    try:
        k.waitpid(pid, nohang=True)
        raise AssertionError("second wait must fail")
    except ChildProcessError:
        pass
    print("PASS: zombie lifecycle (R -> Z -> reaped, single-wait semantics)")


def test_wnohang_poll_loop():
    k = FakeKernel()
    pids = [k.fork() for _ in range(3)]
    exits = {pids[1]: 7, pids[0]: 1, pids[2]: 3}   # 退出顺序与 fork 顺序不同
    zero_polls = 0
    reaped = {}
    while len(reaped) < 3:
        progressed = False
        for pid in [p for p in exits if p not in reaped]:
            r, w = k.waitpid(pid, nohang=True)
            if r == pid:
                reaped[pid] = wexitstatus(w)
                progressed = True
            else:
                zero_polls += 1
        if not progressed:
            # 模拟时间推进:让下一个子进程退出
            nxt = next(p for p in exits if p not in reaped)
            k.child_exit(nxt, exits[nxt])
    assert reaped == {pids[0]: 1, pids[1]: 7, pids[2]: 3}
    assert zero_polls > 0, "poll loop must observe WNOHANG==0 at least once"
    print("PASS: WNOHANG poll loop reaps out-of-order exits")


def test_sigchld_ign():
    k = FakeKernel()
    k.sigchld_ign = True
    pid = k.fork()
    k.child_exit(pid, 7)
    assert k.procs[pid]["state"] == FakeKernel.REAPED, "SIG_IGN: no zombie"
    try:
        k.waitpid(pid, nohang=True)
        raise AssertionError("wait after SIG_IGN must fail with ECHILD")
    except ChildProcessError:
        pass
    print("PASS: SIGCHLD=SIG_IGN auto-reaps, waitpid -> ECHILD")


def test_adoption_by_init():
    k = FakeKernel()
    parent = k.fork()
    kids = [k.fork() for _ in range(2)]
    for c in kids:
        k.child_exit(c, 0)                      # 两个僵尸
    adopted = k.parent_exits(parent)             # 父进程终止 → init 收养并收尸
    assert sorted(adopted) == sorted(kids)
    assert all(k.procs[c]["state"] == FakeKernel.REAPED for c in kids)
    print("PASS: parent death -> zombies adopted and reaped by init(1)")


def test_stdio_duplication():
    """全缓冲 + fork 复制缓冲区:exit(3) 双份、_exit(2) 单份。"""
    for child_uses_exit3, expect in ((False, 1), (True, 2)):
        parent_buf = ["X"]                       # fork 前写入,未刷
        child_buf = list(parent_buf)             # fork:子进程副本(COW 逻辑同)
        output = []
        if child_uses_exit3:
            output += child_buf                  # exit(3) 刷子进程那份
        output += parent_buf                     # 父进程 fflush 自己那份
        assert len(output) == expect, output
    print("PASS: stdio buffer duplication (exit(3) doubles, _exit(2) not)")


def main():
    test_status_macros()
    test_zombie_lifecycle()
    test_wnohang_poll_loop()
    test_sigchld_ign()
    test_adoption_by_init()
    test_stdio_duplication()
    print("all fork/zombie simulation assertions passed")


if __name__ == "__main__":
    main()
