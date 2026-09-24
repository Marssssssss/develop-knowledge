"""runc create 双进程流程 —— 演示入口。"""

from runc_create import default_config, run, session_ring_params


def show(title):
    print("\n== %s ==" % title)


def dump(child, parent, sock):
    print("   子进程事件:", " → ".join(child.events))
    print("   父进程事件:", " → ".join(parent.events))
    print("   握手:", " → ".join(s.type for _, s in sock.log))
    print("   结果: ierr=%r state=%s" % (parent.ierr, parent.state))


def main():
    show("1. 默认流程（无 seccomp）")
    p, c, s = run(default_config())
    dump(c, p, s)

    show("2. 带 seccomp 且无 NoNewPrivileges（尽早）")
    p1, c1, _ = run(default_config(seccomp={"listener": True}))
    print("   seccomp_init 位置: %d / 共 %d 步，在 finalize_namespace 之前=%s"
          % (c1.events.index("seccomp_init"), len(c1.events),
             c1.events.index("seccomp_init") < c1.events.index("finalize_namespace")))

    show("3. 带 seccomp 且有 NoNewPrivileges（尽可能晚，紧贴 execve）")
    p2, c2, _ = run(default_config(seccomp={"listener": True}, no_new_privs=True))
    print("   seccomp_init 位置: %d / 共 %d 步，在 lookpath 之后=%s"
          % (c2.events.index("seccomp_init"), len(c2.events),
             c2.events.index("seccomp_init") > c2.events.index("lookpath")))

    show("4. procReady 之前就死掉")
    p3, c3, _ = run(default_config(die_before="procReady"))
    print("   子进程停在中途:", c3.events[-1])
    print("   父进程 ierr:", p3.ierr, "| state:", p3.state)

    show("5. 会话 keyring 权限位")
    for ns in (("NEWNS",), ("NEWUSER",)):
        name, keep, perms = session_ring_params("abc123", ns)
        print("   namespaces=%-10s → %s keep=%#x perms=%#x" % (ns, name, keep, perms))

    show("6. exec fifo 与 fd 收紧")
    p4, c4, _ = run(default_config(passed_files_count=2))
    print("   fifo 写入:", c4.fifo_writes, "| UnsafeCloseFrom 起点:",
          [e for e in c4.events if e.startswith("unsafe_close")])


if __name__ == "__main__":
    main()
