"""最小有栈协程:Python 侧 = ucontext 协议状态机 + 无栈对照实验。

Windows 上没有 ucontext,本文件做两件事:
  A. 按 man7 makecontext(3) 的协议复刻一个"最小 ucontext":
     makecontext(int 参数限制)/ swapcontext(存回自己、激活目标)/
     uc_link(函数 return 后激活后继;None 即线程退出),并跑一个
     与 C 版同构的调度器场景,断言切换轨迹
  B. 无栈协程的词法限制实验:helper 里的 yield 不会传导到外层调用者,
     证明"挂起点必须词法可见"是无栈模型的本质约束
"""

import types

MAIN = "__main__"


# ---------- A. 最小 ucontext 协议复刻 ----------

class MakecontextError(TypeError):
    pass


class MiniUContext:
    """ucontext 协议的最小用户态复刻。

    协程体的"切出点"用 yield 表达(swapcontext 语义);
    函数自然返回 = 激活 uc_link(None 时等价线程退出)。
    """

    def __init__(self):
        self.ctxs = {}          # name -> {gen, uc_link, stack}
        self.trace = []         # ('swap', a, b) / ('ret', a)

    def makecontext(self, name, func, args, stack_size, uc_link):
        """makecontext(3):入口 + argc 个 int 参数 + 栈 + 后继上下文。"""
        for a in args:
            if type(a) is not int:
                # man 手册:变参"只能是 int",传其他类型是 UB(此处直接拒绝)
                raise MakecontextError(
                    f"makecontext args must be int, got {type(a).__name__}")
        self.ctxs[name] = {
            "gen": func(*args),
            "uc_link": uc_link,
            "stack": stack_size,
        }

    def run(self, first):
        """调度器:从 first 开始,直到控制权回到 main 或线程退出。"""
        current = first
        while True:
            ctx = self.ctxs[current]
            try:
                kind, frm, to = next(ctx["gen"])
            except StopIteration:
                # 协程函数 return -> 激活 uc_link
                self.trace.append(("ret", current))
                nxt = ctx["uc_link"]
                if nxt is None:
                    return "thread-exit"      # uc_link=NULL:线程(进程)退出
                if nxt == MAIN:
                    return "main"             # 控制权回到主上下文
                current = nxt
                continue
            assert kind == "swap" and frm == current, "switch integrity"
            self.trace.append(("swap", frm, to))
            if to == MAIN:
                return "main"                # swapcontext 切回主上下文
            current = to


def test_ucontext_protocol():
    ROUNDS = 2
    done = {}

    def co_body(idx):
        """与 C 版同构:非末轮 swap 回调度器,末轮走 return(-> uc_link)。"""
        for r in range(ROUNDS):
            done[idx] = done.get(idx, 0) + 1
            if r < ROUNDS - 1:
                yield ("swap", f"co{idx}", MAIN)   # swapcontext 回主上下文
        # return:由 run() 激活 uc_link

    u = MiniUContext()
    for i in range(3):
        u.makecontext(f"co{i}", co_body, (i,), 64 * 1024, uc_link=MAIN)

    # 调度器:round-robin 重启未跑满的协程
    for _ in range(ROUNDS + 1):
        advanced = False
        for i in range(3):
            if done.get(i, 0) < ROUNDS:
                assert u.run(f"co{i}") == "main", "control returns to main"
                advanced = True
        if not advanced:
            break

    assert done == {0: 2, 1: 2, 2: 2}
    swaps = [t for t in u.trace if t[0] == "swap"]
    rets = [t for t in u.trace if t[0] == "ret"]
    # 每协程 1 次 swap + 1 次 uc_link 返回
    assert len(swaps) == 3 and len(rets) == 3
    assert u.trace == [
        ("swap", "co0", MAIN), ("swap", "co1", MAIN), ("swap", "co2", MAIN),
        ("ret", "co0"), ("ret", "co1"), ("ret", "co2"),
    ], u.trace
    print("PASS: ucontext protocol (swap + uc_link successor, round-robin)")


def test_makecontext_int_only():
    u = MiniUContext()

    def body(x):
        yield ("swap", "bad", MAIN)

    try:
        u.makecontext("bad", body, ("not-an-int",), 4096, MAIN)
        raise AssertionError("non-int arg must be rejected")
    except MakecontextError:
        pass
    print("PASS: makecontext rejects non-int args (int-only per man page)")


def test_uc_link_none_exits():
    def body():
        return          # 直接返回 -> 激活 uc_link
        yield           # 让本函数成为 generator(永不执行)

    u = MiniUContext()
    u.makecontext("co0", body, (), 4096, uc_link=None)   # uc_link = NULL
    # man 手册:后继为 NULL 时"线程退出" -> 整个进程终止的语义
    assert u.run("co0") == "thread-exit"
    print("PASS: uc_link=NULL means thread/process exit")


# ---------- B. 无栈协程的词法限制 ----------

def test_stackless_lexical_limit():
    def helper():
        yield "inner"                     # helper 自己成了 generator

    def outer_naive():
        helper()                          # 只拿到 generator 对象,并未挂起
        yield "outer"

    assert isinstance(helper(), types.GeneratorType)
    got = list(outer_naive())
    assert got == ["outer"], got          # inner 没有传导到 outer 的消费者

    def outer_delegated():
        yield from helper()               # yield from:层层委托才能传导
        yield "outer"

    assert list(outer_delegated()) == ["inner", "outer"]
    print("PASS: stackless limit -- helper's yield never propagates "
          "without yield-from delegation")


def main():
    test_ucontext_protocol()
    test_makecontext_int_only()
    test_uc_link_none_exits()
    test_stackless_lexical_limit()
    print("ucontext simulation + stackless contrast passed")


if __name__ == "__main__":
    main()
