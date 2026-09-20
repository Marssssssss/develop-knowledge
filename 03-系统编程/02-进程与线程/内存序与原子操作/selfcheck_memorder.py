"""内存序自检：用 litmus 测试断言「某个结果是否可能出现」。

断言一律针对结果集合的成员关系 —— 这才是能证伪的命题，
而不是「应该看到什么」这种应然描述。
"""

from memorder_model import (
    Machine, Program, reads, pairs,
    RELAXED, CONSUME, ACQUIRE, RELEASE, ACQ_REL, SEQ_CST,
)

PASS = []


def ok(name, cond):
    if not cond:
        raise AssertionError("FAIL: " + name)
    PASS.append(name)


def eq(name, got, want):
    if got != want:
        raise AssertionError("FAIL: %s got=%r want=%r" % (name, got, want))
    PASS.append("%s (= %r)" % (name, got))


def mp(store_order, load_order):
    """message passing：data 先写，flag 后写；读者先看 flag 再读 data。"""
    m = Machine([
        Program(0, [("store", "data", 1, RELAXED), ("store", "flag", 1, store_order)]),
        Program(1, [("load", "flag", load_order), ("load", "data", RELAXED)]),
    ])
    return pairs(m.explore(), 1, ["flag", "data"])


out = mp(RELEASE, ACQUIRE)
ok("release-acquire 保证 data 可见：(1,0) 不可能", (1, 0) not in out)
ok("读到 flag=1 时必然看到 data=1", (1, 1) in out)

out = mp(RELAXED, RELAXED)
ok("全 relaxed 时 (1,0) 真的会出现", (1, 0) in out)

out = mp(RELEASE, RELAXED)
ok("只有 release 没有 acquire 也不行：(1,0) 仍会出现", (1, 0) in out)
ok("但 (1,1) 也是允许的（不保证 ≠ 不可能）", (1, 1) in out)

# ---- store buffering（Dekker）：两个线程各写一个变量再读对方写的那个 ----
def sb(store_order, load_order, fence=False):
    """store buffering：各写一个变量，再读对方写的那个。"""
    ops0 = [("store", "x", 1, store_order)]
    ops1 = [("store", "y", 1, store_order)]
    if fence:
        ops0.append(("fence", None, 0, SEQ_CST))
        ops1.append(("fence", None, 0, SEQ_CST))
    ops0.append(("load", "y", load_order))
    ops1.append(("load", "x", load_order))
    m = Machine([Program(0, ops0), Program(1, ops1)])
    got = set()
    for o in m.explore():
        a, b = reads(o, 0, "y"), reads(o, 1, "x")
        if a and b:
            got.add((a[0], b[0]))
    return got


out = sb(RELEASE, ACQUIRE)
ok("release 写 + acquire 读挡不住 store buffering：(0,0) 会出现", (0, 0) in out)

out = sb(SEQ_CST, SEQ_CST)
ok("seq_cst 靠单全序排除掉 (0,0)", (0, 0) not in out)
ok("其余三种结果都还在", out == {(0, 1), (1, 0), (1, 1)})

out = sb(RELAXED, RELAXED, fence=True)
ok("relaxed 读写 + seq_cst 栅栏也能排除 (0,0)", (0, 0) not in out)

# ---- RMW：原子性不依赖内存序 ----
m = Machine([Program(0, [("rmw", "x", 1, RELAXED)]),
             Program(1, [("rmw", "x", 1, RELAXED)])])
olds = set()
for o in m.explore():
    olds.add(tuple(sorted(reads(o, 0, "x") + reads(o, 1, "x"))))
eq("两个 relaxed fetch_add 的返回值必定是 {0,1}", olds, {(0, 1)})
ok("没有出现「两个都读到 0」的丢更新", (0, 0) not in olds)

# ---- release sequence：relaxed 的 RMW 能接力，普通写会切断 ----
def release_sequence(t2_op):
    m = Machine([
        Program(0, [("store", "data", 1, RELAXED), ("store", "flag", 1, RELEASE)]),
        Program(1, [t2_op]),
        Program(2, [("load", "flag", ACQUIRE), ("load", "data", RELAXED)]),
    ])
    return pairs(m.explore(), 2, ["flag", "data"])


# 用 +2 区分三种来源：1=T1 的 release 写；2=RMW 读到初值(不在 release sequence 里)；
# 3=RMW 读到 release 写(接上了 release sequence)
out = release_sequence(("rmw", "flag", 2, RELAXED))
ok("acquire 读到 release 写(1) 时 data 必为 1", (1, 0) not in out)
ok("relaxed RMW 接力后：读到 3 也看得到 data=1", (3, 0) not in out)
ok("RMW 若没读到 release 写(值 2)，同步链就没接上：(2,0) 出现", (2, 0) in out)
ok("接力成功的 (3,1) 可达", (3, 1) in out)

out = release_sequence(("store", "flag", 2, RELAXED))
ok("换成 relaxed 普通写就切断了同步：(2,0) 出现", (2, 0) in out)

# ---- coherence：同一线程对同一变量的两次读不能「先新后旧」 ----
m = Machine([Program(0, [("store", "x", 1, RELAXED)]),
             Program(1, [("load", "x", RELAXED), ("load", "x", RELAXED)])])
seqs = set(tuple(reads(o, 1, "x")) for o in m.explore())
ok("CoRR：不会出现 (1,0) 这种时光倒流", (1, 0) not in seqs)
ok("(0,0)/(0,1)/(1,1) 都在", {(0, 0), (0, 1), (1, 1)} <= seqs)

print("内存序自检通过：%d 项" % len(PASS))
for i, name in enumerate(PASS, 1):
    print("  %2d. %s" % (i, name))
