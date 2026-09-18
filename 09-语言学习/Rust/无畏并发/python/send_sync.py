"""Send / Sync 自动 trait 推导引擎 + 共享状态并发的可运行模型。

依据：
  * std::marker::Send — "Types that can be transferred across thread boundaries."
  * std::marker::Sync — "The precise definition is: &T is Send."
    并给出四条关系：
        &T     is Send  iff  T is Sync
        &mut T is Send  iff  T is Send
        &T     is Sync  iff  T is Sync
        &mut T is Sync  iff  T is Sync
  * std::sync::MutexGuard — `impl !Send for MutexGuard<'_, T>`、
    `impl<T: Sync> Sync for MutexGuard<'_, T>`
  * std::cell::RefCell   — `impl<T: Send> Send`、`impl !Sync`
  * The Book ch16-03/16-04 — Rc 非原子故非 Send、Arc 用原子操作、手工实现是 unsafe
"""

PRIMITIVES = {"i32", "u32", "u8", "i64", "u64", "usize", "f64", "bool", "char", "String", "()"}


# ------------------------------------------------------------ 类型小语言解析

def _split_top(s):
    """按顶层逗号切分（对 < > 深度敏感）。"""
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return [x.strip() for x in out if x.strip()]


def parse(s):
    """把 "Arc<Mutex<i32>>" / "&mut Vec<String>" / "*const u8" 解析成 AST。"""
    s = s.strip()
    if s.startswith("&"):
        rest = s[1:].strip()
        mut = False
        if rest.startswith("mut "):
            mut, rest = True, rest[4:].strip()
        return ("ref", parse(rest), mut)
    if s.startswith("*const ") or s.startswith("*mut "):
        head, rest = s.split(" ", 1)
        return ("raw", parse(rest))
    if "<" in s:
        name = s[: s.index("<")]
        inner = s[s.index("<") + 1: s.rindex(">")]
        return ("app", name, [parse(x) for x in _split_top(inner)])
    return ("prim", s)


# ------------------------------------------------------------ 自动 trait 推导

def traits(t):
    """返回 (Send, Sync)。t 可以是字符串或已解析的 AST。"""
    if isinstance(t, str):
        t = parse(t)

    if t[0] == "prim":
        if t[1] not in PRIMITIVES:
            raise ValueError(f"unknown primitive {t[1]!r}")
        return (True, True)                       # 官方：几乎所有基本类型都是

    if t[0] == "raw":
        return (False, False)                     # 官方：裸指针是例外

    if t[0] == "ref":
        _kind, inner, mut = t
        i_send, i_sync = traits(inner)
        # &T 的 Send 取决于 T 的 Sync（共享引用能跨线程 ⇔ T 可被共享）
        # &mut T 的 Send 取决于 T 的 Send（独占权转移）
        # 两者 Sync 都取决于 T 的 Sync
        return (i_sync if not mut else i_send, i_sync)

    _kind, name, args = t
    a = traits(args[0])

    if name == "Rc":
        # 非原子计数：两线程同时 clone 会并发改计数 → UB
        return (False, False)
    if name == "Arc":
        # Arc<T>: Send + Sync 都要求 T: Send + Sync
        return (a[0] and a[1], a[0] and a[1])
    if name in ("Cell", "RefCell"):
        # 内部可变性但非线程安全：可搬走，不可共享
        return (a[0], False)
    if name in ("Mutex", "RwLock"):
        # Mutex<T>: Send + Sync 都只要求 T: Send
        return (a[0], a[0])
    if name == "MutexGuard":
        # 特例：锁守卫不能离开加锁的线程，但 &MutexGuard 可以
        return (False, a[1])
    if name in ("Vec", "Box", "Option"):
        return a
    raise ValueError(f"unknown type constructor {name!r}")


def struct_traits(fields):
    """官方："Any type composed entirely of Send types is automatically Send."

    Sync 同理。字段里只要有一个不是，整体就不是。
    """
    ts = [traits(f) for f in fields]
    return (all(x[0] for x in ts), all(x[1] for x in ts))


def spawn_error(ty):
    """`thread::spawn` 要求闭包捕获物 `F: Send + 'static`。返回 None 或报错串。"""
    send, _sync = traits(ty)
    if send:
        return None
    return (f"error[E0277]: `{ty}` cannot be sent between threads safely\n"
            f"  = help: the trait `Send` is not implemented for `{ty}`")


# ------------------------------------------------------------ 互斥与死锁

class Deadlock(Exception):
    pass


class Poisoned(Exception):
    pass


class Mutex:
    def __init__(self, name, value=0):
        self.name = name
        self.value = value
        self.owner = None
        self.poisoned = False

    def lock(self, thread, graph):
        """返回 Guard；抢不到就把 thread → owner 记进 wait-for 图。"""
        if self.owner is None:
            if self.poisoned:
                raise Poisoned(f"PoisonError<MutexGuard<{self.name}>>")
            self.owner = thread
            graph.pop(thread, None)
            return Guard(self, thread)
        if self.owner is thread:
            raise Deadlock(f"{thread} 重复锁同一个 {self.name}（std Mutex 不可重入）")
        graph[thread] = self.owner            # wait-for 边
        return None


class Guard:
    """MutexGuard：Deref 到内部数据，Drop 时释放锁。"""

    def __init__(self, mutex, thread):
        self.mutex = mutex
        self.thread = thread
        self.released = False

    def get(self):
        if self.released:
            raise RuntimeError("use after drop: guard already released")
        return self.mutex.value

    def set(self, v):
        if self.released:
            raise RuntimeError("use after drop: guard already released")
        self.mutex.value = v

    def release(self):
        if not self.released:
            self.mutex.owner = None
            self.released = True


def panic_while_holding(guard):
    """持锁线程 panic → 锁被 poison：官方原文 "The call to lock would fail if
    another thread holding the lock panicked." """
    guard.mutex.poisoned = True
    guard.release()


def has_cycle(graph):
    """wait-for 图里存在环 ⇒ 死锁。"""
    for start in list(graph):
        seen, node = set(), start
        while node in graph and node not in seen:
            seen.add(node)
            node = graph[node]
        if node in seen:
            return True
    return False


def run_two_threads(order_t1, order_t2):
    """两个线程按各自的加锁顺序跑一遍，返回 (是否死锁, wait-for 图)。

    order 形如 ["A", "B"]：先锁 A 再锁 B，拿到第二把锁后释放两把。
    """
    locks = {n: Mutex(n) for n in set(order_t1) | set(order_t2)}
    graph = {}
    held = {"T1": [], "T2": []}
    # 交错推进：T1 取第一把 → T2 取第一把 → 各自取第二把
    for name, order in (("T1", order_t1), ("T2", order_t2)):
        g = locks[order[0]].lock(name, graph)
        if g is not None:
            held[name].append(g)
    for name, order in (("T1", order_t1), ("T2", order_t2)):
        g = locks[order[1]].lock(name, graph)
        if g is None and has_cycle(graph):
            return True, graph
        if g is not None:
            held[name].append(g)
    return has_cycle(graph), graph
