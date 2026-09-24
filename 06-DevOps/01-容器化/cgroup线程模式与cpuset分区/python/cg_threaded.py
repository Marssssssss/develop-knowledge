"""cgroup v2 线程模式（cgroup.type）与 cpuset 分区（cpuset.cpus.partition）。

逐段转写自 torvalds/linux：
  Documentation/admin-guide/cgroup-v2.rst（135851 B）
    §Threaded 模式（"domain"/"domain threaded"/"threaded"/"domain (invalid)"、
      转 threaded 的两个条件、EOPNOTSUPP、threaded 控制器清单）
    §CPUSET 的 cpuset.cpus.exclusive / cpuset.cpus.exclusive.effective /
      cpuset.cpus.partition（member/root/isolated、local/remote、有效性三条件）
  Documentation/admin-guide/cgroup-v1/cpusets.rst（37976 B）：cpuset 的基本语义
"""

THREADED_CONTROLLERS = {"cpu", "cpuset", "perf_event", "pids"}

DOMAIN = "domain"
DOMAIN_THREADED = "domain threaded"
THREADED = "threaded"
DOMAIN_INVALID = "domain (invalid)"

MEMBER, ROOT, ISOLATED = "member", "root", "isolated"


class CgroupError(Exception):
    def __init__(self, errno, why):
        super().__init__(why)
        self.errno = errno          # 拓扑非法一律 EOPNOTSUPP
        self.why = why


class Cgroup:
    def __init__(self, name, parent=None, cpus=None, all_cpus=None):
        self.name = name
        self.parent = parent
        self.children = []
        self.threaded = False                # 是否已被写成 threaded
        self.subtree_control = set()
        self.procs = set()                   # PID
        self.threads = set()                 # TID
        self.partition = MEMBER
        self.cpus = set(cpus if cpus is not None
                        else (parent.possible_cpus() if parent else
                              set(all_cpus or range(8))))
        self.cpus_exclusive = None           # None 表示未显式设置
        self._created_local = False          # 建分区时父是否为有效 partition root
        self._all_cpus = set(all_cpus or range(8))
        if parent is not None:
            parent.children.append(self)

    # ---------------------------------------------------------------- 基础
    def possible_cpus(self):
        return set(self._all_cpus)

    def is_root(self):
        return self.parent is None

    def thread_root(self):
        """最近的、不是 threaded 的祖先 = threaded domain / thread root。"""
        node = self
        while node is not None and node.threaded:
            node = node.parent
        return node

    def in_threaded_subtree(self):
        return self.thread_root() is not None and \
            any(c.threaded for c in self.thread_root().children)

    def populated(self):
        return bool(self.procs or self.threads)

    def domain_children(self):
        return [c for c in self.children if not c.threaded]

    def type_str(self):
        """cgroup.type 的读值。"""
        if self.threaded:
            return THREADED
        if self.is_root():
            return DOMAIN_THREADED if self.children and \
                any(c.threaded for c in self.children) else DOMAIN
        parent = self.parent
        if parent.threaded or (not parent.is_root() and parent.type_str() == DOMAIN_THREADED):
            # 父是 threaded / threaded domain ⇒ 本 cgroup 不能当 domain 用
            if not self.threaded:
                return DOMAIN_INVALID
        if any(c.threaded for c in self.children):
            return DOMAIN_THREADED
        if self.subtree_control and THREADED_CONTROLLERS & self.subtree_control \
                and self.populated():
            return DOMAIN_THREADED
        return DOMAIN

    # ---------------------------------------------------------------- 线程模式
    def make_threaded(self):
        """echo threaded > cgroup.type 的两条判据。"""
        if self.threaded:
            raise CgroupError("EOPNOTSUPP", "already threaded")
        if self.is_root():
            raise CgroupError("EOPNOTSUPP", "root can not be threaded")
        parent = self.parent
        parent_is_threaded_domain = (not parent.is_root()
                                     and parent.type_str() == DOMAIN_THREADED)
        if not (parent.threaded or parent_is_threaded_domain
                or parent.type_str() == DOMAIN or parent.is_root()):
            raise CgroupError("EOPNOTSUPP", "parent is not a valid domain")
        # 文档：父是未线程化的 domain 时不得有域控制器或已填充的 domain 子 cgroup，
        # 但根 cgroup 豁免这条
        if parent.type_str() == DOMAIN and not parent.is_root():
            domain_ctrls = parent.subtree_control - THREADED_CONTROLLERS
            populated_domain_kids = [c for c in parent.domain_children()
                                     if c.populated()]
            if domain_ctrls or populated_domain_kids:
                raise CgroupError(
                    "EOPNOTSUPP",
                    "parent is an unthreaded domain with domain controllers "
                    "enabled or populated domain children")
        self.threaded = True
        return self

    def enable(self, controller):
        """只有 threaded 控制器能开在 threaded 子树里。"""
        if (self.threaded or self.type_str() == DOMAIN_THREADED
                or self.thread_root().threaded):
            if controller not in THREADED_CONTROLLERS:
                raise CgroupError("EOPNOTSUPP",
                                  "%s is not a threaded controller" % controller)
        self.subtree_control.add(controller)

    def add_proc(self, pid):
        if self.type_str() == DOMAIN_INVALID:
            raise CgroupError("EOPNOTSUPP", "cgroup is in an invalid topology")
        if not self.cpus_effective():
            raise CgroupError("ENOSPC", "cpuset.cpus.effective is empty")
        self.procs.add(pid)

    def add_thread(self, tid):
        if self.type_str() == DOMAIN_INVALID:
            raise CgroupError("EOPNOTSUPP", "cgroup is in an invalid topology")
        self.threads.add(tid)

    # ---------------------------------------------------------------- cpuset
    def cpus_effective(self):
        own = set(self.cpus)
        if self.parent is not None:
            own &= self.parent.cpus_effective()
        return own

    def requested_exclusive(self):
        return set(self.cpus_exclusive) if self.cpus_exclusive is not None \
            else set(self.cpus)

    def cpus_exclusive_effective(self):
        """只能用父的 exclusive.effective 里存在的那些，且不能与兄弟抢。"""
        if self.parent is None:
            return set(self._all_cpus)
        avail = set(self.parent.cpus_exclusive_effective())
        # 兄弟申请过的独占 CPU 不能再拿（只取兄弟的申请集，避免互相递归）
        taken = set()
        for sib in self.parent.children:
            if sib is not self:
                taken |= sib.requested_exclusive()
        return (avail & self.requested_exclusive()) - taken

    def is_partition_root(self):
        return self.is_root() or self.partition in (ROOT, ISOLATED)

    def ancestor_has_partition_root(self):
        node, seen = self.parent, []
        while node is not None and not node.is_root():
            seen.append(node)
            node = node.parent
        return any(n.partition in (ROOT, ISOLATED) for n in seen)

    def partition_invalid_reason(self):
        if not self.is_partition_root() or self.is_root():
            return None
        local = self.parent.is_partition_root() and \
            self.parent.partition_invalid_reason() is None
        if self._created_local:
            # local 分区要求父始终是有效 partition root，父转 member 就失效
            if not local:
                return "parent is not a valid partition root"
        elif self.ancestor_has_partition_root():
            return "remote partition under a partition root"
        if not self.cpus_exclusive_effective():
            return "cpuset.cpus.exclusive.effective is empty"
        if not self.cpus_effective() and self.populated():
            return "cpuset.cpus.effective is empty with tasks"
        if local and not self.cpus_effective() and not self.populated():
            return None
        return None

    def read_partition(self):
        if not self.is_partition_root():
            return MEMBER
        reason = self.partition_invalid_reason()
        if reason:
            return "%s invalid (%s)" % (self.partition, reason)
        return self.partition

    def set_partition(self, value):
        if self.is_root():
            raise CgroupError("EOPNOTSUPP", "root cgroup's partition state "
                                            "cannot be changed")
        if value not in (MEMBER, ROOT, ISOLATED):
            raise CgroupError("EINVAL", "invalid partition value %r" % value)
        self.partition = value
        if value in (ROOT, ISOLATED):
            self._created_local = self.parent.is_partition_root() and \
                self.parent.partition_invalid_reason() is None
        else:
            self._created_local = False
        return self

    def tasks(self):
        """partition 里的任务数（含自身与后代）。"""
        n = len(self.procs) + len(self.threads)
        for c in self.children:
            if c.partition in (ROOT, ISOLATED):
                continue        # 独立的 partition root 不算在本 partition 内
            n += c.tasks()
        return n
