"""cgroup v2 线程模式与 cpuset 分区 —— 演示入口。"""

from cg_threaded import (ISOLATED, MEMBER, ROOT, Cgroup, CgroupError)


def show(title):
    print("\n== %s ==" % title)


def main():
    show("1. cgroup.type 的四态")
    root = Cgroup("/", cpus=set(range(8)))
    a = Cgroup("A", parent=root)
    b = Cgroup("B", parent=a)
    print("   A 新建:", a.type_str())
    b.make_threaded()
    print("   B 写成 threaded:", b.type_str())
    print("   父 A 变成:", a.type_str())
    c = Cgroup("C", parent=b)
    print("   B 之下新建的 C:", c.type_str())

    show("2. 转 threaded 的两个条件")
    for desc, setup in (("父开了域控制器 memory", "ctrl"),
                        ("父有已填充的 domain 子 cgroup", "kid")):
        r = Cgroup("/", cpus=set(range(8)))
        p = Cgroup("P", parent=r)
        if setup == "ctrl":
            p.enable("memory")
        else:
            p.children.append(Cgroup("K", parent=p))
            p.children[-1].procs.add(9)
        q = Cgroup("Q", parent=p)
        try:
            q.make_threaded()
            print("   %-32s -> 允许" % desc)
        except CgroupError as e:
            print("   %-32s -> %s %s" % (desc, e.errno, e.why))

    show("3. partition 的读值")
    r = Cgroup("/", cpus=set(range(8)))
    a = Cgroup("A", parent=r, cpus={0, 1, 2, 3})
    b = Cgroup("B", parent=r, cpus={4, 5, 6, 7})
    for cg, val in ((a, ROOT), (b, ISOLATED)):
        cg.set_partition(val)
        print("   %s <- %-9s 读值=%s  exclusive.effective=%s"
              % (cg.name, val, cg.read_partition(),
                 sorted(cg.cpus_exclusive_effective())))

    show("4. 抢同一批 CPU 的兄弟")
    r2 = Cgroup("/", cpus=set(range(8)))
    x = Cgroup("X", parent=r2, cpus={0, 1})
    y = Cgroup("Y", parent=r2, cpus={2, 3})
    y.cpus_exclusive = {0, 1}
    x.set_partition(ROOT)
    y.set_partition(ROOT)
    print("   X:", x.read_partition(), sorted(x.cpus_exclusive_effective()))
    print("   Y:", y.read_partition(), sorted(y.cpus_exclusive_effective()))

    show("5. 父转 member 会连累子 local 分区")
    r3 = Cgroup("/", cpus=set(range(8)))
    p = Cgroup("P", parent=r3, cpus={0, 1, 2, 3})
    p.set_partition(ROOT)
    c1 = Cgroup("C1", parent=p, cpus={0, 1})
    c1.set_partition(ROOT)
    print("   P=root 时 C1:", c1.read_partition())
    p.set_partition(MEMBER)
    print("   P=member 时 C1:", c1.read_partition())
    p.set_partition(ROOT)
    print("   P 恢复 root 后 C1:", c1.read_partition())


if __name__ == "__main__":
    main()
