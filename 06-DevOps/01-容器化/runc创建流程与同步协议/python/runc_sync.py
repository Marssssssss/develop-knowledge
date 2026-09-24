"""runc 父子同步协议（libcontainer/sync.go 的转写）。

原文：opencontainers/runc `libcontainer/sync.go`（5382 B，main 分支）
同步常量成对出现，注释里的握手图是：

	     [  child  ] <-> [   parent   ]
	procMountPlease  --> [open(2) or open_tree(2) and configure mount]
	                 <-- procMountFd        file: mountfd
	procSeccomp      --> [forward fd to listenerPath]   （无返回同步）
	procHooks        --> [run hooks]      <-- procHooksDone
	procReady        --> [final setup]    <-- procRun
	procSeccomp      --> [pidfd_getfd()]  <-- procSeccompDone
"""

import json

PROC_ERROR = "procError"
PROC_READY = "procReady"
PROC_RUN = "procRun"
PROC_HOOKS = "procHooks"
PROC_HOOKS_DONE = "procHooksDone"
PROC_MOUNT_PLEASE = "procMountPlease"
PROC_MOUNT_FD = "procMountFd"
PROC_SECCOMP = "procSeccomp"
PROC_SECCOMP_DONE = "procSeccompDone"

SYNC_FLAG_HAS_FD = 1 << 0
SHUT_WR = 1

STATE_CREATING = "creating"
STATE_CREATED = "created"
STATE_RUNNING = "running"
STATE_STOPPED = "stopped"

SECCMP_FD_NAME = "seccompFd"


class InitError(Exception):
    """initError：encoding/json 无法反序列化成 error，故包一层。"""

    def __init__(self, message=""):
        super().__init__(message)
        self.message = message


class SyncEof(Exception):
    """doReadSync 遇到 io.EOF：管道关闭，parseSync 用它退出循环。"""


class SyncT:
    def __init__(self, type_, arg=None, file=None, flags=0):
        self.type = type_
        self.arg = arg
        self.file = file
        self.flags = flags

    def has_fd(self):
        return (self.flags & SYNC_FLAG_HAS_FD) != 0

    def __repr__(self):
        s = "type:" + self.type
        if self.flags:
            s += " flags:0b" + bin(self.flags)[2:]
        if self.arg is not None:
            s += " arg:" + json.dumps(self.arg, sort_keys=True)
        if self.file is not None:
            s += " file:" + str(self.file)
        return s


class SyncSocket:
    """syncSocket 的模型：两个方向的队列 + 各自的写端关闭标志。"""

    def __init__(self):
        self.to_child = []
        self.to_parent = []
        self.parent_wr_open = True
        self.child_wr_open = True
        self.log = []            # (方向, SyncT)

    # ---------------------------------------------------------------- 父侧
    def parent_write(self, sync):
        if not self.parent_wr_open:
            raise OSError("sync pipe closed")
        sync.flags |= SYNC_FLAG_HAS_FD if sync.file is not None else 0
        self.log.append(("parent->child", sync))
        self.to_child.append(sync)

    def parent_read(self):
        if self.to_parent:
            return self.to_parent.pop(0)
        if not self.child_wr_open:
            raise SyncEof()
        raise OSError("would block: parent read with no data")

    def parent_shutdown_wr(self):
        self.parent_wr_open = False

    # ---------------------------------------------------------------- 子侧
    def child_write(self, sync):
        if not self.child_wr_open:
            raise OSError("sync pipe closed")
        sync.flags |= SYNC_FLAG_HAS_FD if sync.file is not None else 0
        self.log.append(("child->parent", sync))
        self.to_parent.append(sync)

    def child_read(self):
        if self.to_child:
            return self.to_child.pop(0)
        if not self.parent_wr_open:
            raise SyncEof()
        raise OSError("would block: child read with no data")

    def child_close(self):
        """`_ = l.pipe.Close()`：关闭管道即向父进程宣告 init 完成。"""
        self.child_wr_open = False


def do_read_sync(reader):
    """doReadSync：解出一个 syncT，procError 还原成 initError。"""
    try:
        sync = reader()
    except SyncEof:
        raise
    if sync.type == PROC_ERROR:
        if sync.arg is None:
            raise InitError("procError missing error payload")
        raise InitError(sync.arg.get("message", ""))
    return sync


def read_sync_full(reader, expected):
    sync = do_read_sync(reader)
    if sync.type != expected:
        raise OSError("unexpected synchronisation flag: got %r, expected %r"
                      % (sync.type, expected))
    return sync


def read_sync(reader, expected):
    """readSync：非预期的 arg / file 都算错误。"""
    sync = read_sync_full(reader, expected)
    if sync.arg is not None:
        raise OSError("sync %s had unexpected argument passed" % expected)
    if sync.file is not None:
        raise OSError("sync %s had unexpected file passed" % expected)
    return sync
