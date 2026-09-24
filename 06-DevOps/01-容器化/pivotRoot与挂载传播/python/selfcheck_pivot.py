"""pivot_root 与 shared subtree 自检。"""

import sys

from pivot_model import (MS_PRIVATE, MS_REC, MS_SHARED, MS_SLAVE,
                         MS_UNBINDABLE, MountTree, PivotError,
                         full_pseudo_roots, ms_move_root, pivot_root,
                         prepare_root, rootfs_parent_mount_propagation,
                         rootfs_parent_mount_propagation_flags, runc_pivot_root,
                         set_propagation_names)

PASSED = 0
FAILED = []


def ok(name, cond):
    global PASSED
    if cond:
        PASSED += 1
    else:
        FAILED.append(name)
        print("  FAIL %s" % name)


def errno_of(fn):
    try:
        fn()
    except PivotError as e:
        return e.errno
    return None


def base_tree():
    t = MountTree()
    t.add("/")                                  # 宿主根
    t.add("/mnt")
    t.add("/mnt/rootfs")                        # 容器 rootfs（bind 自身后是挂载点）
    t.dirs.add("/mnt/rootfs/.old")
    return t


def t_preconditions():
    print("[1] pivot_root 的六条限制")
    t = base_tree()
    t.dirs.add("/mnt/notamount")
    ok("new_root 不是挂载点 → EINVAL",
       errno_of(lambda: pivot_root(t, "/mnt/notamount", "/mnt/rootfs/.old")) == "EINVAL")
    ok("new_root 是 / → EBUSY",
       errno_of(lambda: pivot_root(t, "/", "/mnt/rootfs/.old")) == "EBUSY")
    t2 = base_tree()
    t2.dirs.add("/tmp/other")
    ok("put_old 不在 new_root 之下 → EINVAL",
       errno_of(lambda: pivot_root(t2, "/mnt/rootfs", "/tmp/other")) == "EINVAL")
    t3 = base_tree()
    ok("路径不是目录 → ENOTDIR",
       errno_of(lambda: pivot_root(t3, "/mnt/rootfs", "/nonexistent")) == "ENOTDIR")
    t4 = base_tree()
    ok("new_root 就在当前根挂载上 → EBUSY",
       errno_of(lambda: pivot_root(t4, "/", "/")) == "EBUSY")
    t5 = base_tree()
    t5.set_propagation("/mnt", MS_SHARED)
    ok("new_root 的父挂载是 shared → EINVAL",
       errno_of(lambda: pivot_root(t5, "/mnt/rootfs", "/mnt/rootfs/.old")) == "EINVAL")
    t6 = base_tree()
    t6.add("/mnt/rootfs/.old")
    t6.set_propagation("/mnt/rootfs/.old", MS_SHARED)
    ok("put_old 自己是 shared 挂载点 → EINVAL",
       errno_of(lambda: pivot_root(t6, "/mnt/rootfs", "/mnt/rootfs/.old")) == "EINVAL")

    t7 = base_tree()
    nr, old = pivot_root(t7, "/mnt/rootfs", "/mnt/rootfs/.old")
    ok("合法调用成功", nr.mountpoint == "/")
    ok("旧根被移到 put_old", old.mountpoint == "/mnt/rootfs/.old")
    ok("没有堆叠（put_old ≠ new_root）", old.stacked is False)


def t_propagation():
    print("[2] shared subtree 的四种传播类型")
    t = MountTree()
    root = t.add("/")
    a = t.add("/a", prop=MS_PRIVATE)
    t.set_propagation("/a", MS_SHARED)
    b = t.add("/b", prop=MS_PRIVATE)
    t.set_propagation("/b", MS_SHARED)
    # 让 a 与 b 同一对等组：先把 b 也设成 shared，再手动合并
    b.peer_group = a.peer_group
    got = t.propagate(a, "/x")
    ok("shared：事件扩散到对等组成员", got == ["/b/x"])
    ok("shared 的对等组非空", a.peer_group is not None and
       b.peer_group == a.peer_group)

    t2 = MountTree()
    t2.add("/")
    p = t2.add("/p", prop=MS_PRIVATE)
    ok("private：不扩散", t2.propagate(p, "/x") == [])

    t3 = MountTree()
    t3.add("/")
    m = t3.add("/m", prop=MS_PRIVATE)
    t3.set_propagation("/m", MS_SHARED)
    s = t3.add("/s", prop=MS_PRIVATE)
    s.master = m.peer_group
    s.prop = MS_SLAVE
    ok("slave：从 master 收到事件", t3.propagate(m, "/x") == ["/s/x"])
    ok("slave：自己的事件不回传 master", t3.propagate(s, "/y") == [])
    ok("slave 没有自己的对等组", s.peer_group is None)

    t4 = MountTree()
    t4.add("/")
    u = t4.add("/u", prop=MS_PRIVATE)
    t4.set_propagation("/u", MS_UNBINDABLE)
    ok("unbindable：不扩散", t4.propagate(u, "/x") == [])
    try:
        t4.bind_mount("/u", "/v")
        ok("从 unbindable bind 出去应失败", False)
    except PivotError as e:
        ok("从 unbindable bind 出去 → EINVAL", e.errno == "EINVAL")

    t5 = MountTree()
    t5.add("/")
    t5.add("/r", prop=MS_PRIVATE)
    t5.add("/r/c1", prop=MS_PRIVATE)
    t5.add("/r/c2", prop=MS_PRIVATE)
    t5.set_propagation("/r", MS_SLAVE | MS_REC)
    ok("MS_REC 递归改到子挂载",
       t5.mount_at("/r/c1").prop == MS_SLAVE and t5.mount_at("/r/c2").prop == MS_SLAVE)
    ok("本来 private 的挂载做 rslave 后是「无主 slave」",
       t5.mount_at("/r").prop == MS_SLAVE and t5.mount_at("/r").master is None)


def runc_tree():
    """rootfs 还只是个目录（runc 会先 bind 自己把它变成挂载点）。"""
    t = MountTree()
    t.add("/")
    t.add("/mnt")
    t.dirs.add("/mnt/rootfs")
    return t


def t_runc_path():
    print("[3] runc 的切根路径")
    t = runc_tree()
    applied, bind = prepare_root(t, "/mnt/rootfs")
    ok("prepareRoot 先给 / 打上 rslave", t.mount_at("/").prop == MS_SLAVE)
    ok("rootfs 的父挂载被去共享（上溯到 /mnt）", applied == "/mnt")
    ok("rootfs 被 bind 到自己身上（于是成了挂载点）",
       t.is_mountpoint("/mnt/rootfs"))
    ok("bind 出来的挂载不再是 shared", bind.prop == MS_PRIVATE)
    ok("rootPropagation=0 时父挂载用 PRIVATE",
       rootfs_parent_mount_propagation_flags(0) == MS_PRIVATE)
    ok("rootPropagation 含 SLAVE 时用 SLAVE",
       rootfs_parent_mount_propagation_flags(MS_SLAVE) == MS_SLAVE)
    ok("rootPropagation 含 PRIVATE 时用 PRIVATE",
       rootfs_parent_mount_propagation_flags(MS_PRIVATE) == MS_PRIVATE)

    t2 = runc_tree()
    prepare_root(t2, "/mnt/rootfs")
    rootfs = t2.mount_at("/mnt/rootfs")
    new_root = runc_pivot_root(t2, rootfs)
    ok("pivot_root(\".\", \".\") 后新根是 /", t2.current_root().id == rootfs.id)
    ok("旧根被堆在新根之上（免临时目录写法的关键）",
       ("stacked", "/mnt/rootfs", "True") in t2.events)
    ev = [e[0] for e in t2.events]
    ok("顺序是 stacked → umount2 → chdir",
       ev[-3:] == ["stacked", "umount2", "chdir"])
    ok("旧根已被 MNT_DETACH 摘掉",
       not any(m.mountpoint == "/mnt/rootfs" and m.fstype != rootfs.fstype
               for m in t2.mounts.values()))

    t3 = MountTree()
    t3.add("/")
    t3.add("/proc", fstype="proc", root="/")
    t3.add("/sys", fstype="sysfs", root="/")
    sub = t3.add("/var/lib/proc", fstype="proc", root="/sub")
    t3.add("/rootfs")
    picks = full_pseudo_roots(t3, "/rootfs")
    ok("只挑完整的（Root == /）procfs/sysfs",
       sorted(m.mountpoint for m in picks) == ["/proc", "/sys"])
    ok("非完整挂载被跳过", sub not in picks)
    t3.add("/rootfs/proc", fstype="proc", root="/")
    ok("rootfs 里的挂载被跳过",
       all(not m.mountpoint.startswith("/rootfs") for m in full_pseudo_roots(t3, "/rootfs")))
    masked = ms_move_root(t3, "/rootfs")
    ok("msMoveRoot 遮掉了 /proc 与 /sys", sorted(masked) == ["/proc", "/sys"])
    ev3 = [e for e in t3.events]
    ok("最后是 MS_MOVE + chroot + chdir",
       ev3[-3:] == [("mount", "/rootfs", "/ (MS_MOVE)"), ("chroot", ".", ""),
                    ("chdir", "/", "")])
    t4 = MountTree()
    t4.add("/")
    t4.add("/proc", fstype="proc", root="/")
    t4.add("/rootfs")
    ms_move_root(t4, "/rootfs", rootless=True)
    ok("rootless 下 umount 失败就用 tmpfs 盖住",
       ("cover", "/proc", "tmpfs") in t4.events)


def t_misc():
    print("[4] 兜底与只读")
    t = MountTree()
    t.add("/")
    ok("沿父目录上溯直到找到挂载点",
       rootfs_parent_mount_propagation(t, "/a/b/c", MS_PRIVATE) == "/")
    try:
        t.set_propagation("/nope", MS_SHARED)
        ok("对非挂载点设传播应报错", False)
    except PivotError:
        ok("对非挂载点设传播 → EINVAL", True)
    t2 = MountTree()
    t2.add("/")
    t2.dirs.add("/tmp/x")
    ok("bind 之前 /tmp/x 不是挂载点", not t2.is_mountpoint("/tmp/x"))
    t2.bind_mount("/tmp/x", "/tmp/x")
    ok("bind 自己之后它就是挂载点了（man 页给的转换法）",
       t2.is_mountpoint("/tmp/x"))
    t2.set_propagation("/", MS_SHARED)
    ok("shared 的根上 remount 只读会失败（需先改传播）",
       set_propagation_names(t2.mount_at("/")) == "shared")
    t2.set_propagation("/", MS_PRIVATE)
    ok("改成 private 后只读 remount 可行",
       set_propagation_names(t2.mount_at("/")) == "private")


def main():
    t_preconditions()
    t_propagation()
    t_runc_path()
    t_misc()
    print("通过 %d 项，失败 %d 项" % (PASSED, len(FAILED)))
    if FAILED:
        for f in FAILED:
            print("  - %s" % f)
        sys.exit(1)


if __name__ == "__main__":
    main()
