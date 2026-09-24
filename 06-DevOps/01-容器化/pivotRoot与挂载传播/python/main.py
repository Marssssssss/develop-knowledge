"""pivot_root 与 shared subtree —— 演示入口。"""

from pivot_model import (MS_PRIVATE, MS_REC, MS_SHARED, MS_SLAVE,
                         MS_UNBINDABLE, MountTree, PivotError,
                         full_pseudo_roots, ms_move_root, pivot_root,
                         prepare_root, runc_pivot_root, set_propagation_names)


def show(title):
    print("\n== %s ==" % title)


def tree_with_rootfs(mount_rootfs=True):
    t = MountTree()
    t.add("/")
    t.add("/mnt")
    if mount_rootfs:
        t.add("/mnt/rootfs")
    else:
        t.dirs.add("/mnt/rootfs")
    t.dirs.add("/mnt/rootfs/.old")
    return t


def main():
    show("1. 六条限制的 errno 分派")
    cases = [("new_root 不是挂载点", "/mnt/plain", "/mnt/rootfs/.old", True),
             ("new_root 是 /", "/", "/mnt/rootfs/.old", False),
             ("put_old 不在 new_root 之下", "/mnt/rootfs", "/tmp/x", True)]
    for name, nr, po, add_dir in cases:
        t = tree_with_rootfs()
        if add_dir:
            t.dirs.add(nr)
            t.dirs.add(po)
        try:
            pivot_root(t, nr, po)
            print("   %-24s -> 成功" % name)
        except PivotError as e:
            print("   %-24s -> %-7s %s" % (name, e.errno, e.why))

    t = tree_with_rootfs()
    t.set_propagation("/mnt", MS_SHARED)
    try:
        pivot_root(t, "/mnt/rootfs", "/mnt/rootfs/.old")
    except PivotError as e:
        print("   %-24s -> %-7s %s" % ("父挂载是 MS_SHARED", e.errno, e.why))

    show("2. 合法调用后的树变换")
    t = tree_with_rootfs()
    new_root, old = pivot_root(t, "/mnt/rootfs", "/mnt/rootfs/.old")
    print("   新根挂载点:", new_root.mountpoint, "| 旧根挂载点:", old.mountpoint)
    print("   当前根 id == rootfs id:", t.current_root().id == new_root.id)

    show("3. 四种传播类型")
    t = MountTree()
    t.add("/")
    a = t.add("/a")
    t.set_propagation("/a", MS_SHARED)
    b = t.add("/b")
    t.set_propagation("/b", MS_SHARED)
    b.peer_group = a.peer_group
    s = t.add("/s")
    s.master, s.prop = a.peer_group, MS_SLAVE
    p = t.add("/p", prop=MS_PRIVATE)
    u = t.add("/u", prop=MS_PRIVATE)
    t.set_propagation("/u", MS_UNBINDABLE)
    for m in (a, s, p, u):
        print("   %-3s %-11s 在它下面挂载 /x 会扩散到 %s"
              % (m.mountpoint, set_propagation_names(m), t.propagate(m, "/x") or "（无）"))

    show("4. runc 的 pivotRoot（\".\", \".\" 免临时目录写法）")
    t = tree_with_rootfs(mount_rootfs=False)
    applied, bind = prepare_root(t, "/mnt/rootfs")
    print("   / 的传播类型:", set_propagation_names(t.mount_at("/")))
    print("   父挂载去共享作用于:", applied)
    print("   rootfs bind 自身后是挂载点:", t.is_mountpoint("/mnt/rootfs"))
    runc_pivot_root(t, t.mount_at("/mnt/rootfs"))
    print("   事件序列:", t.events)
    print("   切根后 / 是:", t.current_root().mountpoint)

    show("5. --no-pivot 的退路 msMoveRoot")
    t = MountTree()
    t.add("/")
    t.add("/proc", fstype="proc", root="/")
    t.add("/sys", fstype="sysfs", root="/")
    t.add("/var/lib/proc", fstype="proc", root="/sub")
    t.add("/rootfs")
    print("   需要遮掉的完整伪文件系统:",
          [m.mountpoint for m in full_pseudo_roots(t, "/rootfs")])
    print("   rootless 下:", ms_move_root(t, "/rootfs", rootless=True))
    print("   事件尾部:", t.events[-3:])


if __name__ == "__main__":
    main()
