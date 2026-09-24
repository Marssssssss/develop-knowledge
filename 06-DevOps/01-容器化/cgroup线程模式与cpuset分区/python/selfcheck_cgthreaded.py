"""cgroup v2 线程模式与 cpuset 分区自检。"""

import sys

from cg_threaded import (DOMAIN, DOMAIN_INVALID, DOMAIN_THREADED, ISOLATED,
                         MEMBER, ROOT, THREADED, THREADED_CONTROLLERS, Cgroup,
                         CgroupError)

PASSED = 0
FAILED = []


def ok(name, cond):
    global PASSED
    if cond:
        PASSED += 1
    else:
        FAILED.append(name)
        print("  FAIL %s" % name)


def err_of(fn):
    try:
        fn()
    except CgroupError as e:
        return e
    return None


def t_type_basics():
    print("[1] cgroup.type 的四种取值")
    root = Cgroup("/", cpus=set(range(8)))
    ok("根 cgroup 默认是 domain", root.type_str() == DOMAIN)
    a = Cgroup("A", parent=root)
    ok("新建的 cgroup 是 domain", a.type_str() == DOMAIN)
    ok("threaded 控制器共 4 个",
       THREADED_CONTROLLERS == {"cpu", "cpuset", "perf_event", "pids"})

    b = Cgroup("B", parent=a)
    b.make_threaded()
    ok("写成 threaded 后类型是 threaded", b.type_str() == THREADED)
    ok("threaded 不可逆", err_of(b.make_threaded) is not None)
    ok("父 cgroup 变成 domain threaded", a.type_str() == DOMAIN_THREADED)
    c = Cgroup("C", parent=b)
    ok("threaded 之下的 domain 子 cgroup 是 domain (invalid)",
       c.type_str() == DOMAIN_INVALID)
    ok("invalid 拓扑的 cgroup 不能放进程",
       err_of(lambda: c.add_proc(1)) is not None)
    ok("根 cgroup 不能被写成 threaded", err_of(root.make_threaded) is not None)


def t_thread_conditions():
    print("[2] 转 threaded 的两个条件")
    root = Cgroup("/", cpus=set(range(8)))
    a = Cgroup("A", parent=root)
    a.enable("memory")                       # 域控制器
    b = Cgroup("B", parent=a)
    e = err_of(b.make_threaded)
    ok("父是未线程化的 domain 且开了域控制器 → EOPNOTSUPP",
       e is not None and e.errno == "EOPNOTSUPP")

    root2 = Cgroup("/", cpus=set(range(8)))
    a2 = Cgroup("A", parent=root2)
    d = Cgroup("D", parent=a2)
    d.add_proc(7)
    b2 = Cgroup("B", parent=a2)
    e2 = err_of(b2.make_threaded)
    ok("父有已填充的 domain 子 cgroup → EOPNOTSUPP",
       e2 is not None and e2.errno == "EOPNOTSUPP")

    root3 = Cgroup("/", cpus=set(range(8)))
    a3 = Cgroup("A", parent=root3)
    b3 = Cgroup("B", parent=a3)
    ok("干净的 domain 父可以转 threaded", b3.make_threaded() is b3)
    ok("thread root 是最近的未线程化祖先", b3.thread_root() is a3)

    # 根 cgroup 豁免"不能有已填充 domain 子"的限制
    root4 = Cgroup("/", cpus=set(range(8)))
    x = Cgroup("X", parent=root4)
    x.add_proc(1)
    y = Cgroup("Y", parent=root4)
    ok("根 cgroup 豁免该限制（可直接转 threaded）",
       y.make_threaded() is y)

    a3.enable("cpu")
    ok("threaded 子树里只能开 threaded 控制器",
       err_of(lambda: a3.enable("memory")) is not None)
    ok("cpu 属于 threaded 控制器", "cpu" in THREADED_CONTROLLERS)


def t_partition_validity():
    print("[3] cpuset.cpus.partition 的有效性")
    root = Cgroup("/", cpus=set(range(8)))
    ok("根 cgroup 恒为 partition root", root.is_partition_root())
    ok("根 cgroup 的 partition 状态不可改",
       err_of(lambda: root.set_partition(ROOT)) is not None)

    a = Cgroup("A", parent=root, cpus={0, 1, 2, 3})
    ok("非根 cgroup 初始是 member", a.read_partition() == MEMBER)
    a.set_partition(ROOT)
    ok("父是根（有效 partition root）⇒ 这是 local 分区",
       a.read_partition() == ROOT)

    # 空 exclusive.effective → invalid
    root2 = Cgroup("/", cpus=set(range(8)))
    b = Cgroup("B", parent=root2, cpus={0, 1})
    c = Cgroup("C", parent=root2, cpus={2, 3})
    b.set_partition(ROOT)
    c.cpus_exclusive = {0, 1}            # 与 B 抢，落空
    c.set_partition(ROOT)
    ok("与兄弟抢同一批 CPU 时 exclusive.effective 为空",
       not c.cpus_exclusive_effective())
    ok("读值变成 root invalid", c.read_partition().startswith("root invalid"))
    ok("invalid 的原因写在括号里",
       "exclusive.effective is empty" in c.read_partition())

    # 有任务但 cpus.effective 为空
    root3 = Cgroup("/", cpus=set(range(8)))
    d = Cgroup("D", parent=root3, cpus={0, 1})
    d.cpus_exclusive = {0, 1}            # 显式给出独占集，避开"exclusive 为空"那条
    d.set_partition(ROOT)
    d.procs.add(1)
    d.cpus = set()                       # 外部事件（hotplug/改 cpus）让 effective 变空
    ok("有任务但 cpus.effective 为空 → invalid",
       "cpuset.cpus.effective is empty" in d.read_partition())
    d.procs.clear()
    ok("任务迁走后同一配置重新有效（第 3 条只约束有任务的情况）",
       d.read_partition() == ROOT)


def t_partition_local_remote():
    print("[4] local 与 remote 分区")
    root = Cgroup("/", cpus=set(range(8)))
    a = Cgroup("A", parent=root, cpus={0, 1, 2, 3})
    a.set_partition(ROOT)
    ok("A 是有效 partition root（父是根）", a.read_partition() == ROOT)
    b = Cgroup("B", parent=a, cpus={0, 1})
    b.set_partition(ROOT)
    ok("A 之下建的是 local 分区", b.read_partition() == ROOT)

    # remote：父不是 partition root，但祖先里也没有 partition root
    root2 = Cgroup("/", cpus=set(range(8)))
    m = Cgroup("M", parent=root2, cpus={0, 1, 2, 3})
    n = Cgroup("N", parent=m, cpus={4, 5})
    n.cpus_exclusive = {4, 5}
    m.cpus_exclusive = {0, 1, 2, 3, 4, 5}
    n.set_partition(ROOT)
    ok("父不是 partition root 且祖先也没有 ⇒ remote 分区合法",
       n.read_partition() == ROOT)
    ok("此时 M 仍是 member", m.read_partition() == MEMBER)

    # remote 不能建在 local 分区之下
    root3 = Cgroup("/", cpus=set(range(8)))
    p = Cgroup("P", parent=root3, cpus=set(range(8)))
    p.set_partition(ROOT)
    q = Cgroup("Q", parent=p, cpus={0, 1})
    r = Cgroup("R", parent=q, cpus={0})
    r.set_partition(ROOT)
    ok("祖先里已有 partition root ⇒ remote 建不出来",
       r.read_partition().startswith("root invalid"))
    ok("原因是 remote partition under a partition root",
       "remote partition under a partition root" in r.read_partition())

    plain = Cgroup("s", parent=root3, cpus={2})
    ok("member 不是 partition root", not plain.is_partition_root())
    plain.set_partition(ISOLATED)
    ok("isolated 也是 partition root", plain.is_partition_root())


def t_partition_transitions():
    print("[5] 状态迁移与失效传播")
    root = Cgroup("/", cpus=set(range(8)))
    a = Cgroup("A", parent=root, cpus={0, 1, 2, 3})
    a.set_partition(ROOT)
    a.set_partition(ISOLATED)
    ok("root → isolated 允许", a.read_partition() == ISOLATED)
    a.set_partition(ROOT)
    ok("isolated → root 允许", a.read_partition() == ROOT)
    a.set_partition(MEMBER)
    ok("root → member 允许", a.read_partition() == MEMBER)
    ok("非法值被拒", err_of(lambda: a.set_partition("bogus")) is not None)

    root2 = Cgroup("/", cpus=set(range(8)))
    p = Cgroup("P", parent=root2, cpus={0, 1, 2, 3})
    p.set_partition(ROOT)
    c1 = Cgroup("C1", parent=p, cpus={0, 1})
    c1.set_partition(ROOT)
    ok("P 有效时子 local 分区有效", c1.read_partition() == ROOT)
    p.set_partition(MEMBER)
    ok("父转 member 后子 local 分区失效",
       c1.read_partition().startswith("root invalid"))
    p.set_partition(ROOT)
    ok("父恢复成 partition root 后子分区恢复有效",
       c1.read_partition() == ROOT)


def t_exclusive_effective():
    print("[6] exclusive.effective 的继承与互斥")
    root = Cgroup("/", cpus=set(range(8)))
    ok("根的 exclusive.effective 是全部 possible CPU",
       root.cpus_exclusive_effective() == set(range(8)))
    a = Cgroup("A", parent=root, cpus={0, 1, 2, 3})
    ok("未显式设 exclusive 时按 cpus 取", a.cpus_exclusive_effective() == {0, 1, 2, 3})
    b = Cgroup("B", parent=root, cpus={4, 5})
    ok("兄弟拿到的互不重叠", b.cpus_exclusive_effective() == {4, 5})
    sub = Cgroup("S", parent=a, cpus={2, 3})
    ok("子能从父的 exclusive.effective 里切一块",
       sub.cpus_exclusive_effective() == {2, 3})
    outside = Cgroup("O", parent=a, cpus={7})
    ok("父 exclusive.effective 之外的 CPU 拿不到",
       outside.cpus_exclusive_effective() == set())

    ok("cpus.effective 是自身 cpus 与父 effective 的交集",
       Cgroup("T", parent=a, cpus={3, 7}).cpus_effective() == {3})
    ok("cpus 取空时 cpus.effective 为空",
       Cgroup("U", parent=a, cpus=set()).cpus_effective() == set())


def main():
    t_type_basics()
    t_thread_conditions()
    t_partition_validity()
    t_partition_local_remote()
    t_partition_transitions()
    t_exclusive_effective()
    print("通过 %d 项，失败 %d 项" % (PASSED, len(FAILED)))
    if FAILED:
        for f in FAILED:
            print("  - %s" % f)
        sys.exit(1)


if __name__ == "__main__":
    main()
