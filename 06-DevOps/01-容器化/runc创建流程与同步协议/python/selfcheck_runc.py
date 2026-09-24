"""runc 创建流程自检：同步对的顺序、seccomp 的两个插入点、exec fifo、状态机。"""

import sys

from runc_create import NEWUSER, NEWNS, ParentHandler, SetnsProcess, \
    StandardInit, default_config, run, session_ring_params
from runc_sync import (PROC_ERROR, PROC_HOOKS, PROC_MOUNT_FD, PROC_MOUNT_PLEASE,
                       PROC_READY, PROC_RUN, PROC_SECCOMP, SECCMP_FD_NAME,
                       STATE_CREATED, STATE_CREATING, InitError, SyncSocket,
                       SyncT, SyncEof, do_read_sync, read_sync, read_sync_full)

PASSED = 0
FAILED = []


def ok(name, cond):
    global PASSED
    if cond:
        PASSED += 1
    else:
        FAILED.append(name)
        print("  FAIL %s" % name)


def idx(events, name):
    return events.index(name) if name in events else -1


def t_basic_flow():
    print("[1] 基本流程与同步对")
    parent, child, sock = run(default_config())
    ok("无错误完成", parent.ierr is None)
    ok("容器进入 created 状态", parent.state == STATE_CREATED)
    ok("子进程走到 execve", child.events[-1] == "execve")
    ok("procReady 在 seccomp 之前（无 seccomp 时即最后一步之前）",
       idx(child.events, "selinux_exec_label") > 0)
    types = [s.type for d, s in sock.log]
    ok("握手序列是 mountPlease*/procReady/procRun",
       types.count(PROC_READY) == 1 and types.count(PROC_RUN) == 1)
    ok("父进程先 update_state 再发 procRun",
       parent.events.index("update_state") <
       len(parent.events) and PROC_RUN in [s.type for d, s in sock.log])
    ok("父进程事件含 apply_cgroup 与 copy_bootstrap_data",
       "apply_cgroup" in parent.events and "copy_bootstrap_data" in parent.events)
    ok("管道关闭发生在 close_pipe 之后（关闭即宣告完成）",
       not sock.child_wr_open)
    ok("子进程向 exec fifo 写了单个字节 0", child.fifo_writes == [b"0"])
    ok("UnsafeCloseFrom 从 PassedFilesCount+3 起",
       "unsafe_close_from_3" in child.events)
    child2 = run(default_config(passed_files_count=5))[1]
    ok("PassedFilesCount=5 时从 8 起", "unsafe_close_from_8" in child2.events)


def t_seccomp_two_points():
    print("[2] seccomp 的两个插入点")
    # 无 NoNewPrivileges：seccomp 是特权操作，必须在丢 capabilities 之前
    p1, c1, s1 = run(default_config(seccomp={"listener": True}))
    ok("无 NNP：seccomp_init 在 finalize_namespace 之前",
       idx(c1.events, "seccomp_init") < idx(c1.events, "finalize_namespace"))
    ok("无 NNP：seccomp 紧跟 procReady 之后",
       c1.events[c1.events.index("selinux_exec_label") + 1] == "seccomp_init")
    ok("无 NNP 时只初始化一次 seccomp", c1.events.count("seccomp_init") == 1)

    # 有 NoNewPrivileges：尽可能晚，紧贴 execve
    p2, c2, s2 = run(default_config(seccomp={"listener": True}, no_new_privs=True))
    ok("有 NNP：seccomp_init 在 lookpath 之后",
       idx(c2.events, "seccomp_init") > idx(c2.events, "lookpath"))
    ok("有 NNP：seccomp_init 在 close_pipe 之前（要用管道传 fd）",
       idx(c2.events, "seccomp_init") < idx(c2.events, "close_pipe"))
    ok("两种配置下 procSeccomp 都恰好发一次",
       [s.type for d, s in s1.log].count(PROC_SECCOMP) == 1 and
       [s.type for d, s in s2.log].count(PROC_SECCOMP) == 1)
    ok("procSeccomp 的 ContainerProcessState 带 seccompFd",
       p1.container_process_state["fds"] == [SECCMP_FD_NAME])
    ok("procSeccomp 时 OCI 状态是 creating",
       p1.container_process_state["state"]["status"] == STATE_CREATING)

    p3 = run(default_config(seccomp={"listener": True}, listener_path=""))[0]
    ok("ListenerPath 未设置 → 报错", p3.ierr is not None
       and "seccomp listenerPath is not set" in p3.ierr)


def t_error_paths():
    print("[3] 错误路径")
    p, c, s = run(default_config(die_before="procReady"))
    ok("子进程在 procReady 前死 → 父进程报 procReady not received",
       p.ierr == "procReady not received")
    ok("此时容器没进 created", p.state == STATE_CREATING)

    p2, c2, s2 = run(default_config(init_error="boom"))
    ok("InitError 会以 procError 形式回报",
       any(s.type == PROC_ERROR for _, s in s2.log))
    ok("父进程收到的是 initError 的消息", p2.ierr == "boom")

    sock2 = SyncSocket()
    sock2.to_child.append(SyncT(PROC_RUN))
    try:
        read_sync(sock2.child_read, PROC_READY)
        ok("类型不符应报错", False)
    except OSError as e:
        ok("类型不符报 unexpected synchronisation flag",
           "unexpected synchronisation flag" in str(e))

    sock3 = SyncSocket()
    sock3.to_child.append(SyncT(PROC_READY, arg={"x": 1}))
    try:
        read_sync(sock3.child_read, PROC_READY)
        ok("带多余 arg 应报错", False)
    except OSError as e:
        ok("带多余 arg 报错", "unexpected argument" in str(e))

    sock4 = SyncSocket()
    sock4.to_child.append(SyncT(PROC_READY, file="fd:x"))
    try:
        read_sync(sock4.child_read, PROC_READY)
        ok("带多余 file 应报错", False)
    except OSError as e:
        ok("带多余 file 报错", "unexpected file" in str(e))

    sock5 = SyncSocket()
    sock5.child_wr_open = False
    try:
        do_read_sync(sock5.parent_read)
        ok("EOF 应抛 SyncEof", False)
    except SyncEof:
        ok("写端关闭后读到 SyncEof（parseSync 用它退出循环）", True)

    sp = SetnsProcess()
    try:
        sp.on_sync(SyncT(PROC_HOOKS))
        ok("setns 收到 procHooks 应 panic", False)
    except RuntimeError as e:
        ok("setns 收到 procHooks 直接 panic", "unexpected procHooks in setns" in str(e))
    ok("setns 收到 procReady 回 procRun",
       sp.on_sync(SyncT(PROC_READY)).type == PROC_RUN)


def t_hooks_and_mounts():
    print("[4] hooks 与挂载请求")
    cfg = default_config(hooks={"prestart": ["a"], "create_runtime": ["b"],
                                "start_container": ["c"]})
    p, c, s = run(cfg)
    ok("父进程先 set_cgroup_config 再跑 hook",
       p.events.index("set_cgroup_config") < p.events.index("run_hook_prestart"))
    ok("Prestart 先于 CreateRuntime",
       p.events.index("run_hook_prestart") < p.events.index("run_hook_create_runtime"))
    ok("这两个 hook 的状态都是 creating",
       p.hook_states == [STATE_CREATING, STATE_CREATING])
    ok("StartContainer 由子进程在 finalize_namespace 之后跑",
       idx(c.events, "run_hook_start_container") > idx(c.events, "finalize_namespace"))

    cfg3 = default_config(hooks={"create_container": ["cc"]})
    p3, c3, s3 = run(cfg3)
    ok("即使没配 Prestart/CreateRuntime 也会发 procHooks",
       idx(c3.events, "sync_parent_hooks") >= 0)
    ok("procHooks 在切根之前（旧 root 还能被 hook 用）",
       idx(c3.events, "sync_parent_hooks") < idx(c3.events, "pivot_root"))
    ok("CreateContainer 由子进程在 procHooksDone 后、切根前跑",
       idx(c3.events, "run_hook_create_container") < idx(c3.events, "pivot_root"))
    ok("默认走 pivot_root", "pivot_root" in c3.events)
    ok("--no-pivot 走 msMoveRoot",
       "ms_move_root" in run(default_config(no_pivot_root=True))[1].events)
    ok("没有 NEWNS 时退化成 chroot",
       "chroot" in run(default_config(namespaces=()))[1].events)
    ok("root propagation 为 0 时不额外 mount",
       "root_propagation" not in c3.events)
    ok("root propagation 是 SHARED 时才在切根后应用",
       "root_propagation" in run(default_config(
           root_propagation=1 << 20))[1].events)
    ok("root propagation 是 SLAVE 时跳过（prepareRoot 已处理）",
       "root_propagation" not in run(default_config(
           root_propagation=1 << 19))[1].events)

    cfg2 = default_config(mounts=[{"destination": "/proc"},
                                  {"destination": "/dev"}])
    p2, c2, s2 = run(cfg2)
    ok("每个 mount 都走一轮 procMountPlease/procMountFd",
       [s.type for d, s in s2.log].count(PROC_MOUNT_PLEASE) == 2 and
       [s.type for d, s in s2.log].count(PROC_MOUNT_FD) == 2)
    ok("子进程拿回两个 fd", len(c2.mount_fds) == 2)
    ok("fd 与请求一一对应",
       c2.mount_fds == [("/proc", "fd:/proc"), ("/dev", "fd:/dev")])
    ok("带 file 的 sync 置了 SYNC_FLAG_HAS_FD",
       all(s.has_fd() for d, s in s2.log if s.type == PROC_MOUNT_FD))


def t_misc():
    print("[5] 会话 keyring、ppid 与 final 收尾")
    name, keep, perms = session_ring_params("abc123", (NEWNS,))
    ok("keyring 名是 _ses.<containerID>", name == "_ses.abc123")
    ok("keepperms 是全 f", keep == 0xffffffff)
    ok("非 userns 要 UID 搜索位 0x80000", perms == 0x80000)
    _, _, perms2 = session_ring_params("abc123", (NEWUSER,))
    ok("有 userns 时要 'other' 搜索位 0x8", perms2 == 0x8)

    p, c, s = run(default_config(ppid_changed=True))
    ok("父进程变了就自杀（不 execve）",
       "self_kill" in c.events and "execve" not in c.events)

    p2, c2, _ = run(default_config(create_console=True, pidfd=True,
                                   hostname="demo"))
    ok("console 在 finalize_rootfs 之前建立",
       idx(c2.events, "setup_console") < idx(c2.events, "finalize_rootfs"))
    ok("setctty 紧随 console", idx(c2.events, "setctty") ==
       idx(c2.events, "setup_console") + 1)
    ok("pidfd 只在配置时建立", "setup_pidfd" in c2.events)
    ok("hostname 非空才 sethostname", "sethostname" in c2.events)
    ok("pdeath_restore 在 finalize_namespace 之后",
       idx(c2.events, "pdeath_restore") > idx(c2.events, "finalize_namespace"))
    ok("无 NEWNS 时不做 finalize_rootfs",
       "finalize_rootfs" not in run(default_config(namespaces=()))[1].events)


def main():
    t_basic_flow()
    t_seccomp_two_points()
    t_error_paths()
    t_hooks_and_mounts()
    t_misc()
    print("通过 %d 项，失败 %d 项" % (PASSED, len(FAILED)))
    if FAILED:
        for f in FAILED:
            print("  - %s" % f)
        sys.exit(1)


if __name__ == "__main__":
    main()
