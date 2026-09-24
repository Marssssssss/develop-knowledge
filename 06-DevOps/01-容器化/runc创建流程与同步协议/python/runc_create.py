"""runc create 的双进程流程：父进程 initProcess 与子进程 linuxStandardInit。

对照源码（opencontainers/runc main 分支）：
  libcontainer/process_linux.go  initProcess.start() 的 parseSync switch
  libcontainer/standard_init_linux.go  linuxStandardInit.Init() 的顺序
  libcontainer/sync.go  同步常量
建模方式：子进程写 sync 时立刻回调父进程的 parseSync（请求/应答成对，无需并发调度）。
"""

from runc_sync import (PROC_ERROR, PROC_HOOKS, PROC_HOOKS_DONE, PROC_MOUNT_FD,
                       PROC_MOUNT_PLEASE, PROC_READY, PROC_RUN, PROC_SECCOMP,
                       PROC_SECCOMP_DONE, SECCMP_FD_NAME, STATE_CREATED,
                       STATE_CREATING, InitError, SyncSocket, SyncT, read_sync)

NEWUSER = "NEWUSER"
NEWNS = "NEWNS"

MS_UNBINDABLE = 1 << 17
MS_PRIVATE = 1 << 18
MS_SLAVE = 1 << 19
MS_SHARED = 1 << 20


def session_ring_params(container_id, namespaces):
    """getSessionRingParams：有 user ns 要 'other' 搜索位，否则要 UID 搜索位。"""
    if NEWUSER in namespaces:
        newperms = 0x8
    else:
        newperms = 0x80000
    return "_ses." + container_id, 0xffffffff, newperms


class ParentHandler:
    """父进程（runc create 进程）侧的 parseSync 状态机。"""

    def __init__(self, sock, config):
        self.sock = sock
        self.config = config
        self.events = []
        self.seen_proc_ready = False
        self.ierr = None
        self.state = STATE_CREATING
        self.hook_states = []

    def start(self):
        self.events.append("cmd_start")
        self.events.append("apply_cgroup")
        self.events.append("copy_bootstrap_data")
        self.events.append("get_child_pid")
        self.events.append("wait_child_exit")

    def _oci_state(self):
        return {"version": "1.2.0", "pid": self.config.get("pid", 4242),
                "status": self.state}

    def on_sync(self, sync):
        t = sync.type
        if t == PROC_MOUNT_PLEASE:
            self.events.append("open_mount_source")
            self.sock.parent_write(SyncT(PROC_MOUNT_FD,
                                         arg={"destination": sync.arg["destination"]},
                                         file="fd:" + sync.arg["destination"]))
        elif t == PROC_HOOKS:
            self.events.append("set_cgroup_config")
            for hook in ("prestart", "create_runtime"):
                if self.config.get("hooks", {}).get(hook):
                    self.hook_states.append(self._oci_state()["status"])
                    self.events.append("run_hook_" + hook)
            self.sock.parent_write(SyncT(PROC_HOOKS_DONE))
        elif t == PROC_READY:
            self.seen_proc_ready = True
            self.events.append("setup_rlimits")
            self.state = STATE_CREATED
            self.events.append("update_state")
            self.sock.parent_write(SyncT(PROC_RUN))
        elif t == PROC_SECCOMP:
            if not self.config.get("listener_path"):
                raise OSError("seccomp listenerPath is not set")
            if sync.arg is None:
                raise OSError("sync %q is missing an argument" % t)
            self.events.append("pidfd_getfd")
            self.sock.parent_write(SyncT(PROC_SECCOMP_DONE))
            s = self._oci_state()
            s["status"] = STATE_CREATING   # 源码显式覆盖：initProcessStartTime 还没设
            self.container_process_state = {
                "fds": [SECCMP_FD_NAME], "pid": s["pid"], "state": s}
        elif t == PROC_ERROR:
            self.ierr = sync.arg.get("message", "")
        else:
            raise OSError("invalid JSON payload from child")

    def finish(self):
        self.sock.parent_shutdown_wr()
        if not self.seen_proc_ready and self.ierr is None:
            self.ierr = "procReady not received"
        return self.ierr


class SetnsProcess:
    """setns（runc exec）路径：不参与 procHooks。"""

    def on_sync(self, sync):
        if sync.type == PROC_HOOKS:
            raise RuntimeError("unexpected procHooks in setns")
        if sync.type == PROC_READY:
            return SyncT(PROC_RUN)
        raise OSError("unexpected synchronisation flag: got %r" % sync.type)


class StandardInit:
    """子进程（runc init）侧：linuxStandardInit.Init() 的顺序。"""

    def __init__(self, sock, parent, config):
        self.sock = sock
        self.parent = parent
        self.config = config
        self.events = []
        self.fifo_writes = []
        self.mount_fds = []

    def _w(self, sync):
        self.sock.child_write(sync)
        if self.parent is not None:
            self.parent.on_sync(sync)

    def init(self):
        c = self.config
        self.events.append("join_session_keyring")
        self.events.append("setup_network")
        self.events.append("setup_route")
        self.events.append("prepare_rootfs")
        for m in c.get("mounts", []):
            self._w(SyncT(PROC_MOUNT_PLEASE, arg=m))
            got = self.sock.child_read()
            self.mount_fds.append((got.arg["destination"], got.file))
        # syncParentHooks：挂载已就绪但尚未切根，旧 root 仍可被 hook 操作
        self.events.append("sync_parent_hooks")
        self._w(SyncT(PROC_HOOKS))
        read_sync(self.sock.child_read, PROC_HOOKS_DONE)
        if c.get("hooks", {}).get("create_container"):
            self.events.append("run_hook_create_container")
        if c.get("no_pivot_root"):
            self.events.append("ms_move_root")
        elif NEWNS in c.get("namespaces", ()):
            self.events.append("pivot_root")
        else:
            self.events.append("chroot")
        rp = c.get("root_propagation", 0)
        if rp and not (rp & (MS_PRIVATE | MS_SLAVE)):
            self.events.append("root_propagation")
        if c.get("create_console"):
            self.events.append("setup_console")
            self.events.append("setctty")
        if c.get("pidfd"):
            self.events.append("setup_pidfd")
        if NEWNS in c.get("namespaces", ()):
            self.events.append("finalize_rootfs")
        if c.get("hostname"):
            self.events.append("sethostname")
        self.events.append("apparmor")
        self.events.append("sysctls")
        self.events.append("readonly_paths")
        self.events.append("mask_paths")
        self.events.append("pdeath_get")
        if c.get("no_new_privs"):
            self.events.append("no_new_privs")
        self.events.append("scheduler")
        self.events.append("ioprio")
        self.events.append("personality")
        self.events.append("mempolicy")
        if c.get("die_before") == "procReady":
            self.sock.child_close()
            return
        if c.get("init_error"):
            raise InitError(c["init_error"])
        # syncParentReady：必须在 seccomp 应用之前，因为之后就不能读写 socket 了
        self._w(SyncT(PROC_READY))
        read_sync(self.sock.child_read, PROC_RUN)
        self.events.append("selinux_exec_label")
        if c.get("seccomp") and not c.get("no_new_privs"):
            self.events.append("seccomp_init")
            self._w(SyncT(PROC_SECCOMP, arg={"fd": 7}))
            read_sync(self.sock.child_read, PROC_SECCOMP_DONE)
        self.events.append("finalize_namespace")
        self.events.append("pdeath_restore")
        if c.get("hooks", {}).get("start_container"):
            self.events.append("run_hook_start_container")
        if c.get("ppid_changed"):
            self.events.append("self_kill")
            self.sock.child_close()
            return
        self.events.append("lookpath")
        if c.get("seccomp") and c.get("no_new_privs"):
            self.events.append("seccomp_init")
            self._w(SyncT(PROC_SECCOMP, arg={"fd": 7}))
            read_sync(self.sock.child_read, PROC_SECCOMP_DONE)
        self.events.append("close_pipe")
        self.sock.child_close()
        self.events.append("close_logpipe")
        self.events.append("reopen_fifo")
        self.fifo_writes.append(b"0")
        self.events.append("close_fifo")
        self.events.append("unsafe_close_from_%d"
                           % (c.get("passed_files_count", 0) + 3))
        self.events.append("execve")

    def init_raising(self):
        """把 InitError 以 procError 的形式回报给父进程（doWriteSync 的路径）。"""
        try:
            self.init()
        except InitError as e:
            self._w(SyncT(PROC_ERROR, arg={"message": e.message}))


def run(config):
    sock = SyncSocket()
    parent = ParentHandler(sock, config)
    child = StandardInit(sock, parent, config)
    parent.start()
    try:
        child.init_raising()
    except (OSError, RuntimeError) as e:      # parseSync 的 fn 返回错误即中止
        if parent.ierr is None:
            parent.ierr = str(e)
    ierr = parent.finish()
    return parent, child, sock


def default_config(**kw):
    cfg = {"namespaces": (NEWNS,), "container_id": "abc123",
           "no_new_privs": False, "seccomp": None, "pid": 4242,
           "passed_files_count": 0, "listener_path": "/run/seccomp.sock"}
    cfg.update(kw)
    return cfg
