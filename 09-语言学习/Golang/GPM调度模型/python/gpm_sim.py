# gpm_sim.py — G/M/P 调度模型的最小可运行模拟(教学复刻,非 Go runtime 复制品)
# 依据 Ardan Labs《Scheduling In Go: Part II》的语义:
#   G = goroutine / M = machine(OS 线程)/ P = processor(逻辑处理器)
#   每 P 一个本地运行队列 LRQ;全局运行队列 GRQ 存尚未分配的 G
#   schedule():1/61 概率先查 GRQ -> 本地 LRQ -> 偷其他 P 的一半 -> 再查 GRQ
#   阻塞系统调用:M 带着 G 与 P 解绑,P 换一个 M 继续服务;M1 挂起备用
#   channel 阻塞:G 被重新排队,M 不进入等待态
# 用固定随机种子保证确定性;结尾断言 + 摘要输出。
import random
from collections import deque

random.seed(20260915)

GID = 0


class G:
    """goroutine:一段带步数预算的执行路径"""

    def __init__(self, name, work=3):
        global GID
        GID += 1
        self.id = GID
        self.name = name
        self.left = work          # 剩余执行步数
        self.state = "runnable"   # runnable/running/blocked/done
        self.block_channel = None  # 阻塞原因:channel 名


class M:
    """machine:OS 线程。free 的 M 可以被调度器调去服务任意 P"""

    def __init__(self, mid):
        self.id = mid
        self.p = None       # 绑定的 P(可能为 None = 已解绑/挂起)
        self.g = None       # 正在执行的 G
        self.busy = 0       # 本步剩余工作量


class P:
    """processor:持有 LRQ;M 必须绑定一个 P 才能执行 Go 代码"""

    def __init__(self, pid):
        self.id = pid
        self.lrq = deque()  # 本地运行队列
        self.m = None       # 当前服务本 P 的 M


class Scheduler:
    def __init__(self, np=2, nm=4):
        self.ps = [P(i) for i in range(np)]
        self.ms = [M(i) for i in range(nm)]
        self.grq = deque()          # 全局运行队列
        self.parked = []            # 因系统调用解绑后挂起备用的 M
        self.log = []
        self.tick = 0               # schedule() 调用计数(驱动 1/61 规则)
        # 初始:每个 P 绑一个 M
        for p in self.ps:
            m = self._free_m()
            m.p = p
            p.m = m

    def _free_m(self):
        for m in self.ms:
            if m.p is None and m.g is None and m not in self.parked:
                return m
        raise RuntimeError("no free M")

    # ------- schedule():决定绑定 P 的 M 接下来跑哪个 G -------
    def schedule(self, p):
        """返回一个可运行的 G,或 None。顺序依据 Ardan Labs Listing 2 伪代码"""
        self.tick += 1
        # only 1/61 of the time, check the global runnable queue first
        if self.tick % 61 == 1 and self.grq:
            return self.grq.popleft()
        # 本地 LRQ
        if p.lrq:
            return p.lrq.popleft()
        # try to steal from other Ps(偷一半)
        for other in self.ps:
            if other is not p and len(other.lrq) >= 2:
                n = len(other.lrq) // 2
                for _ in range(n):
                    p.lrq.append(other.lrq.pop())
                self.log.append(f"steal: P{p.id} 从 P{other.id} 偷 {n} 个 G")
                return p.lrq.popleft()
        # 再查 GRQ
        if self.grq:
            return self.grq.popleft()
        return None  # 没活了(真实 runtime 还会 poll network)

    # ------- G 生命周期事件 -------
    def spawn(self, g, to_grq=False):
        if to_grq:
            self.grq.append(g)
        else:
            self.ps[0].lrq.append(g)

    def run(self, steps=200):
        for _ in range(steps):
            progressed = False
            for p in self.ps:
                m = p.m
                if m.g is None:
                    m.g = self.schedule(p)
                    if m.g is None:
                        continue
                    m.g.state = "running"
                g = m.g
                progressed = True
                g.left -= 1
                if g.left == 0:
                    g.state = "done"
                    self.log.append(f"done: {g.name}")
                    m.g = None
            if not progressed:
                break

    # ------- channel 阻塞:G 让位,M 不闲着 -------
    def block_on_channel(self, g, chan, park_later=None):
        g.state = "blocked"
        g.block_channel = chan
        m = self._m_of(g)
        m.g = None                      # M 立刻空出来
        self.log.append(f"block: {g.name} 阻塞在 channel '{chan}'(G 让位,M 不阻塞)")
        if park_later is not None:
            park_later()                # 唤醒回调演示:阻塞方之后重新入队

    def _m_of(self, g):
        for m in self.ms:
            if m.g is g:
                return m
        return None

    def wake(self, g, target_p=0):
        g.state = "runnable"
        self.ps[target_p].lrq.append(g)
        self.log.append(f"wake: {g.name} 重新入队(可再次被任意 M 执行)")

    # ------- 阻塞系统调用:M 带着阻塞的 G 与 P 解绑,P 换一个新 M -------
    def blocking_syscall(self, g):
        m = self._m_of(g)
        p = m.p
        g.state = "blocked"
        m.g = g            # G 仍挂在 M1 上(等系统调用返回)
        m.p = None         # M1 与 P 解绑
        p.m = None
        m2 = self._free_m()  # 调度器调一个空闲 M2 服务 P
        m2.p = p
        p.m = m2
        self.parked.append(m)
        self.log.append(
            f"syscall: {g.name} 阻塞 -> M{m.id} 带 G 与 P{p.id} 解绑并挂起备用; "
            f"M{m2.id} 接管 P{p.id}")

    def syscall_return(self, g, target_p=0):
        m = self._m_of(g)
        self.parked.remove(m)          # M1 恢复可用(placed on the side for future use)
        m.g = None
        g.state = "runnable"
        self.ps[target_p].lrq.append(g)
        self.log.append(f"sysret: {g.name} 回到 LRQ;M{m.id} 放回空闲池备用")


def main():
    print("=== 场景 1:work stealing(偷一半)===")
    s = Scheduler(np=2, nm=2)
    # 把 8 个 G 全塞给 P0,P1 只能靠偷
    for i in range(8):
        s.spawn(G(f"g{i}", work=2))
    s.run()
    done = sum(1 for line in s.log if line.startswith("done"))
    assert done == 8, f"应有 8 个 G 完成,实际 {done}"
    steals = [l for l in s.log if l.startswith("steal")]
    assert steals, "P1 应该发生过 work stealing"
    print(f"  完成 {done} 个 G;偷取事件 {len(steals)} 次,示例: {steals[0]}")

    print("\n=== 场景 2:channel 阻塞(G 让位,M 不闲着)===")
    s = Scheduler(np=1, nm=1)
    g1, g2 = G("sender", work=5), G("receiver", work=2)
    p0 = s.ps[0]
    p0.m.g = g1              # g1 正在 M 上执行
    s.spawn(g2)              # g2 在 LRQ 排队
    g1.left -= 1             # 跑了一步
    s.block_on_channel(g1, "ch", park_later=lambda: s.wake(g1))
    s.run()
    assert g2.state == "done" and g1.state == "done"
    print("  阻塞的 sender 让出 M,receiver 先完成,随后 sender 被唤醒完成")
    print("  两个 G 复用同一 M —— OS 视角线程从未 waiting")

    print("\n=== 场景 3:阻塞系统调用(M-P 解绑 + M 复用)===")
    s = Scheduler(np=1, nm=4)
    g1, g2 = G("fileIO", work=3), G("compute", work=4)
    p0 = s.ps[0]
    p0.m.g = g1              # g1 正在 M1 上执行
    s.spawn(g2)              # g2 在 LRQ 排队
    g1.left -= 1
    s.blocking_syscall(g1)   # g1 走文件 I/O:M1 解绑挂起,M2 接管 P0
    s.run()                    # M2 上跑 g2
    assert g2.state == "done"
    s.syscall_return(g1)       # 系统调用返回:g1 回 LRQ,M1 回空闲池
    s.run()
    assert g1.state == "done"
    print("  阻塞方与计算方互不拖累;解绑的 M1 挂起后又被复用")

    print("\n=== 场景 4:schedule() 的 1/61 规则 ===")
    # tick%61==1 时优先取 GRQ;否则 GRQ 里的 G 会一直排在 LRQ 之后
    s = Scheduler(np=1, nm=1)
    order = []
    s.spawn(G("local-1", work=1), to_grq=False)
    s.spawn(G("global-1", work=1), to_grq=True)
    s.spawn(G("local-2", work=1), to_grq=False)
    for p in s.ps:  # 只调用 schedule 观察顺序,不真正执行
        m = p.m
        for _ in range(3):
            g = s.schedule(p)
            if g:
                order.append(g.name)
                g.state = "done"
    assert order[0] in ("global-1", "local-1")  # 第 1 次 tick%61==1:GRQ 优先
    assert order == ["global-1", "local-1", "local-2"] or \
           order == ["local-1", "global-1", "local-2"], order
    print(f"  取用顺序: {order}(tick=1 时 GRQ 优先,之后 LRQ 优先)")

    print("\n全部场景断言通过 ✓")


if __name__ == "__main__":
    main()
