"""C11/C++ 内存序的可执行模型。

依据（本轮实读）：
  * cppreference《std::memory_order》—— 六种序的定义、release sequence、
    seq_cst 的单全序、强序(x86 TSO)/弱序(ARM/Power)上的实现差异
  * C11 n1570 §5.1.2.4 / §7.17（synchronizes-with / happens-before / 六种 memory_order）

模型口径（官方未定量处显式标注，本模型取其中一种自洽读法）：
  * 每个变量有一条**全局修改序**，写按执行顺序追加；
  * 每个线程有一份「可见集合」view 和一份「已见水位」seen：
      - relaxed 写进了内存，但**传播到别的线程是可选的、逐条独立的**；
      - 传播只带这一条写本身，并把它所在变量的水位抬上去（coherence：见了新的就不能再见旧的）；
      - release 写把「写入那一刻本线程已见的全部写」打包成 pub；
        **只有 acquire/seq_cst 的读**读到它时才把 pub 并入本线程（这就是 synchronizes-with）；
        relaxed 的读不建立同步 —— 所以光有 release 没 acquire 是没用的；
      - **release sequence**：relaxed 的 RMW 读到一个 release 写时会继承它的 pub，
        而 relaxed 的普通写不会继承，于是会切断同步链；
      - RMW 一律读该变量修改序上的最后一个值（这就是 fetch_add 不会丢更新的原因）；
      - seq_cst 采用「写入立即对全部线程可见 + 单一全序」这一充分口径实现
        （真实机器靠全栅栏，见 README 性能边界）；
      - consume 本模型按「与 acquire 同处理」这一保守口径实现，不单独断言。
  * 结果 = 穷举所有交错与传播后得到的**结果集合**；断言一律针对「某结果是否在集合里」。
"""

RELAXED, CONSUME, ACQUIRE, RELEASE, ACQ_REL, SEQ_CST = (
    "relaxed", "consume", "acquire", "release", "acq_rel", "seq_cst")


class Program:
    """一个线程的程序；op = (kind, var, value, order)，value 对 rmw 可为函数。"""

    def __init__(self, tid, ops):
        self.tid = tid
        self.ops = ops

    def __len__(self):
        return len(self.ops)


class Machine:
    def __init__(self, programs, init=0):
        self.programs = programs
        self.nthread = len(programs)
        self.vars = sorted({op[1] for p in programs for op in p.ops if op[1] is not None})
        self.writes = []
        for v in self.vars:
            self.writes.append((len(self.writes), v, init, None, False))
        init_view = frozenset(w[0] for w in self.writes)
        blank = tuple(0 for _ in range(self.nthread * len(self.vars)))
        self.start = (tuple([0] * self.nthread),
                      tuple(init_view for _ in range(self.nthread)),
                      tuple(self.writes), blank, ())

    def _idx(self, tid, var):
        return tid * len(self.vars) + self.vars.index(var)

    def _raise(self, seen, tid, writes):
        """把一批写并入线程后，按变量抬高已见水位。"""
        new = list(seen)
        for w in writes:
            i = self._idx(tid, w[1])
            if w[0] > new[i]:
                new[i] = w[0]
        return tuple(new)

    def _last_of(self, writes, var):
        last = None
        for w in writes:
            if w[1] == var:
                last = w
        return last

    def explore(self, max_states=600000):
        outcomes, visited, stack = set(), set(), [self.start]
        while stack:
            state = stack.pop()
            if state in visited:
                continue
            visited.add(state)
            if len(visited) > max_states:
                break
            pcs, views, writes, seen, results = state
            if all(pcs[t] >= len(self.programs[t].ops) for t in range(self.nthread)):
                outcomes.add(tuple(results))
                continue

            # (a) 各线程推进下一条指令
            for tid in range(self.nthread):
                ops = self.programs[tid].ops
                pc = pcs[tid]
                if pc >= len(ops):
                    continue
                op = ops[pc]
                if len(op) == 3:                       # (kind, var, order) 简写
                    kind, var, order = op
                    val = None
                else:
                    kind, var, val, order = op
                npcs = tuple(pcs[:tid]) + (pc + 1,) + tuple(pcs[tid + 1:])

                if kind == "store":
                    wid = len(writes)
                    pub = frozenset(views[tid]) if order in (RELEASE, ACQ_REL, SEQ_CST) else None
                    nwrites = writes + ((wid, var, val, pub, order == SEQ_CST),)
                    nviews, nseen = list(views), list(seen)
                    targets = range(self.nthread) if order == SEQ_CST else (tid,)
                    for t in targets:
                        nviews[t] = frozenset(set(nviews[t]) | {wid})
                        i = self._idx(t, var)
                        if wid > nseen[i]:
                            nseen[i] = wid
                    stack.append((npcs, tuple(nviews), nwrites, tuple(nseen), results))

                elif kind == "fence":
                    # seq_cst 栅栏口径：把内存里已有的全部写对本线程可见并抬水位
                    nviews, nseen = list(views), list(seen)
                    for w in writes:
                        nviews[tid] = frozenset(set(nviews[tid]) | {w[0]})
                        i = self._idx(tid, w[1])
                        if w[0] > nseen[i]:
                            nseen[i] = w[0]
                    stack.append((npcs, tuple(nviews), writes, tuple(nseen), results))

                elif kind == "rmw":
                    prev = self._last_of(writes, var)
                    old = prev[2] if prev else 0
                    newval = val(old) if callable(val) else old + val
                    wid = len(writes)
                    if order in (RELEASE, ACQ_REL, SEQ_CST):
                        pub = frozenset(views[tid])
                    else:
                        pub = prev[3] if prev else None      # release sequence 的继承
                    nwrites = writes + ((wid, var, newval, pub, order == SEQ_CST),)
                    nviews = list(views)
                    nviews[tid] = frozenset(set(nviews[tid]) | {wid})
                    nseen = list(seen)
                    i = self._idx(tid, var)
                    if wid > nseen[i]:
                        nseen[i] = wid
                    stack.append((npcs, tuple(nviews), nwrites, tuple(nseen),
                                  results + (("rmw", tid, var, old),)))

                elif kind == "load":
                    floor = seen[self._idx(tid, var)]
                    for w in writes:
                        if w[1] != var or w[0] < floor or w[0] not in views[tid]:
                            continue
                        nviews = list(views)
                        nviews[tid] = frozenset(set(nviews[tid]) | {w[0]})
                        nseen = list(seen)
                        i = self._idx(tid, var)
                        if w[0] > nseen[i]:
                            nseen[i] = w[0]
                        if order in (ACQUIRE, CONSUME, SEQ_CST) and w[3]:
                            # synchronizes-with：release 方此前的所有写一起并入
                            pub = [writes[x] for x in w[3]]
                            nviews[tid] = frozenset(set(nviews[tid]) | {x[0] for x in pub})
                            nseen = list(self._raise(tuple(nseen), tid, pub))
                        stack.append((npcs, tuple(nviews), writes, tuple(nseen),
                                      results + (("load", tid, var, w[2]),)))

            # (b) 传播：把内存中某条写推给还没看到它的线程（只带它自己）
            for tid in range(self.nthread):
                for w in writes:
                    if w[0] in views[tid]:
                        continue
                    nviews = list(views)
                    nviews[tid] = frozenset(set(nviews[tid]) | {w[0]})
                    nseen = list(self._raise(seen, tid, [w]))
                    stack.append((pcs, tuple(nviews), writes, tuple(nseen), results))
        return outcomes


def reads(outcome, tid, var):
    """取某线程对某变量的各次读值（按执行顺序）。"""
    return [r[3] for r in outcome if r[0] in ("load", "rmw") and r[1] == tid and r[2] == var]


def pairs(outcomes, tid, vars_):
    return set(tuple(reads(o, tid, v)[0] if reads(o, tid, v) else None for v in vars_)
               for o in outcomes)
