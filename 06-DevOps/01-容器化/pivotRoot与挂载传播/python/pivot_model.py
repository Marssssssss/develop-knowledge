"""pivot_root 与 shared subtree 传播（runc 切根路径的建模）。

权威来源：
  man7 pivot_root(2)（20967 B）：六条限制、EBUSY/EINVAL/ENOTDIR/EPERM 分派、
                               以及 pivot_root(".", ".") 的免临时目录写法
  opencontainers/runc libcontainer/rootfs_linux.go（52735 B）：
      prepareRoot / rootfsParentMountPropagation / pivotRoot / msMoveRoot / setReadonly
"""

MS_UNBINDABLE = 1 << 17
MS_PRIVATE = 1 << 18
MS_SLAVE = 1 << 19
MS_SHARED = 1 << 20
MS_REC = 1 << 14
MS_BIND = 1 << 12
MS_MOVE = 1 << 13
MS_REMOUNT = 1 << 5
MS_RDONLY = 1
MS_SILENT = 1 << 15
MNT_DETACH = 2

PROP_NAMES = {MS_SHARED: "shared", MS_PRIVATE: "private",
              MS_SLAVE: "slave", MS_UNBINDABLE: "unbindable"}


def set_propagation_names(m):
    """把挂载的传播类型翻译成名字（shared/private/slave/unbindable）。"""
    return PROP_NAMES.get(m.prop, "unknown")


class PivotError(Exception):
    def __init__(self, errno, why):
        super().__init__(why)
        self.errno = errno
        self.why = why


class Mount:
    def __init__(self, mid, mountpoint, fstype="ext4", prop=MS_PRIVATE,
                 root="/", peer_group=None, master=None, parent=None):
        self.id = mid
        self.mountpoint = mountpoint
        self.fstype = fstype
        self.prop = prop
        self.root = root              # mountinfo 的 Root 字段：是否为"完整"挂载
        self.peer_group = peer_group  # 共享对等组的 id（shared 才有）
        self.master = master          # slave 指向的主对等组 id
        self.parent = parent
        self.stacked = False          # pivot_root(".", ".") 后旧根堆在 / 上


class MountTree:
    def __init__(self):
        self.mounts = {}
        self.dirs = {"/"}
        self.next_id = 1
        self.next_peer = 1
        self.root_id = None
        self.events = []              # 传播事件记录，便于断言

    # ---------------------------------------------------------------- 建树
    def add(self, mountpoint, fstype="ext4", prop=MS_PRIVATE, root="/",
            parent=None):
        self.dirs.add(mountpoint)
        m = Mount(self.next_id, mountpoint, fstype, prop, root, parent=parent)
        self.next_id += 1
        self.mounts[m.id] = m
        if mountpoint == "/":
            self.root_id = m.id
        return m

    def current_root(self):
        return self.mounts[self.root_id]

    def mount_at(self, path):
        for m in self.mounts.values():
            if m.mountpoint == path:
                return m
        return None

    def is_mountpoint(self, path):
        return self.mount_at(path) is not None

    def is_dir(self, path):
        return path in self.dirs

    def parent_mount(self, m):
        """父挂载：挂载点的最长前缀且本身是挂载点的那个。"""
        best = None
        for other in self.mounts.values():
            if other.id == m.id:
                continue
            if m.mountpoint.startswith(other.mountpoint.rstrip("/") + "/") or \
                    other.mountpoint == "/":
                if best is None or len(other.mountpoint) > len(best.mountpoint):
                    best = other
        return best or self.current_root()

    # ---------------------------------------------------------------- 传播
    def set_propagation(self, path, flags, recursive=False):
        m = self.mount_at(path)
        if m is None:
            raise PivotError("EINVAL", "%s is not a mount point" % path)
        targets = [m]
        if recursive or (flags & MS_REC):
            targets += [c for c in self.mounts.values()
                        if c.mountpoint.startswith(path.rstrip("/") + "/")
                        or path == "/"]
        for t in targets:
            if flags & MS_SHARED:
                if t.peer_group is None:
                    t.peer_group = self.next_peer
                    self.next_peer += 1
                t.prop = MS_SHARED
            elif flags & MS_PRIVATE:
                t.peer_group, t.master, t.prop = None, None, MS_PRIVATE
            elif flags & MS_SLAVE:
                # 有主就是 slave；本来是 private 的话就是"无主 slave"，行为等同 private
                t.master = t.peer_group
                t.peer_group = None
                t.prop = MS_SLAVE
            elif flags & MS_UNBINDABLE:
                t.peer_group, t.master, t.prop = None, None, MS_UNBINDABLE
        return targets

    def propagate(self, src, event_path):
        """在 src 下挂载/卸载时，事件如何扩散。

        shared：对等组内的其他成员照做；slave：只从 master 收，不回传。
        """
        seen = {src.id}
        created = []
        if src.prop == MS_PRIVATE or src.prop == MS_UNBINDABLE:
            return created
        group = src.peer_group
        if group is not None:
            for peer in self.mounts.values():
                if peer.id in seen or peer.peer_group != group:
                    continue
                seen.add(peer.id)
                created.append(peer.mountpoint + event_path)
            for sl in self.mounts.values():
                if sl.master == group and sl.id not in seen:
                    seen.add(sl.id)
                    created.append(sl.mountpoint + event_path)
                    created += self.propagate(sl, event_path)
        return created

    def mount(self, path, under=None, fstype="tmpfs"):
        """在 under 之下新建挂载，并把事件传播出去。"""
        under = under or self.current_root()
        full = (under.mountpoint.rstrip("/") + path)
        self.dirs.add(full)
        m = self.add(full, fstype, parent=under.id)
        self.events.append(("mount", under.mountpoint, full))
        self.events += [("propagated", under.mountpoint, p)
                        for p in self.propagate(under, path)]
        return m

    def bind_mount(self, src_path, dst_path):
        """bind：源是 unbindable 就 EINVAL。"""
        src = self.mount_at(src_path)
        if src is not None and src.prop == MS_UNBINDABLE:
            raise PivotError("EINVAL", "source mount is unbindable")
        parent = self.parent_mount(
            self.mount_at(dst_path) or Mount(-1, dst_path))
        if parent.prop == MS_UNBINDABLE:
            raise PivotError("EINVAL", "destination parent is unbindable")
        self.dirs.add(dst_path)
        m = self.add(dst_path, src.fstype if src else "bind", src.prop if src else
                     MS_PRIVATE, root=src.root if src else "/")
        return m


def at_or_under(put_old, new_root):
    """man 页：给 put_old 加若干个 '/..' 后缀能得到 new_root。"""
    if put_old == new_root:
        return True
    return put_old.startswith(new_root.rstrip("/") + "/")


def pivot_root(tree, new_root, put_old, cwd="/"):
    """pivot_root(2) 的六条限制与成功后的树变换。"""
    resolved_new = cwd if new_root == "." else new_root
    resolved_old = cwd if put_old == "." else put_old
    if not tree.is_dir(resolved_new) or not tree.is_dir(resolved_old):
        raise PivotError("ENOTDIR", "new_root or put_old is not a directory")
    nr = tree.mount_at(resolved_new)
    if nr is None:
        raise PivotError("EINVAL", "new_root is not a mount point")
    if resolved_new == "/":
        raise PivotError("EBUSY", "new_root is /")
    cur = tree.current_root()
    po = tree.mount_at(put_old)
    if nr.id == cur.id or (po is not None and po.id == cur.id):
        raise PivotError("EBUSY", "new_root or put_old is on the current root mount")
    if not at_or_under(resolved_old, resolved_new):
        raise PivotError("EINVAL", "put_old is not at or underneath new_root")
    if tree.parent_mount(nr).prop == MS_SHARED or \
            tree.parent_mount(cur).prop == MS_SHARED:
        raise PivotError("EINVAL", "parent mount has propagation type MS_SHARED")
    if po is not None and po.prop == MS_SHARED:
        raise PivotError("EINVAL", "put_old is a mount point and has MS_SHARED")
    # 成功：new_root 成为 /，旧根被移到 put_old（runc 的 "." 写法下即堆在 / 上）
    old = cur
    old.mountpoint = resolved_old
    old.stacked = resolved_old == resolved_new
    nr.mountpoint = "/"
    tree.root_id = nr.id
    tree.events.append(("pivot_root", new_root, put_old))
    return nr, old


def bind_self(path):
    """man 页：非挂载点可以 bind 到自己身上变成挂载点。"""
    return "mount --bind %s %s" % (path, path)


# ---------------------------------------------------------------- runc 的路径

def prepare_root(tree, rootfs, root_propagation=0):
    """prepareRoot：/ 变 rslave → rootfs 父挂载去共享 → rootfs bind 自身。"""
    flags = MS_SLAVE | MS_REC if root_propagation == 0 else root_propagation
    tree.set_propagation("/", flags)
    rp = rootfs_parent_mount_propagation_flags(root_propagation)
    applied_to = rootfs_parent_mount_propagation(tree, rootfs, rp)
    bind = tree.bind_mount(rootfs, rootfs)
    bind.prop = MS_PRIVATE
    return applied_to, bind


def rootfs_parent_mount_propagation_flags(root_propagation):
    if root_propagation & MS_SLAVE:
        return MS_SLAVE
    return MS_PRIVATE


def rootfs_parent_mount_propagation(tree, path, flags):
    """EINVAL 表示还不是挂载点，就沿父目录往上找，直到 /。"""
    while True:
        if tree.is_mountpoint(path):
            tree.set_propagation(path, flags)
            return path
        if path == "/":
            raise PivotError("EINVAL", "remount-propagation failed at /")
        path = path.rsplit("/", 1)[0] or "/"


def runc_pivot_root(tree, rootfs_mount):
    """runc 的 pivotRoot：pivot_root(".", ".") → rslave "." → MNT_DETACH → chdir("/")。"""
    new_root, old = pivot_root(tree, ".", ".", cwd=rootfs_mount.mountpoint)
    tree.set_propagation(old.mountpoint, MS_SLAVE | MS_REC)
    tree.events.append(("stacked", old.mountpoint, str(old.stacked)))
    tree.mounts.pop(old.id, None)
    tree.dirs.discard(old.mountpoint)
    tree.events.append(("umount2", ".", "MNT_DETACH"))
    tree.events.append(("chdir", "/", ""))
    return new_root


def full_pseudo_roots(tree, rootfs):
    """msMoveRoot 要遮掉的东西：Root == / 的完整 procfs / sysfs，且不在 rootfs 下。"""
    return [m for m in tree.mounts.values()
            if m.root == "/" and m.fstype in ("proc", "sysfs")
            and not m.mountpoint.startswith(rootfs)]


def ms_move_root(tree, rootfs, rootless=False):
    """--no-pivot 的退路：先遮伪文件系统，再 MS_MOVE + chroot。"""
    masked = []
    for m in full_pseudo_roots(tree, rootfs):
        tree.set_propagation(m.mountpoint, MS_SLAVE | MS_REC)
        if rootless:
            tree.events.append(("cover", m.mountpoint, "tmpfs"))
            m.fstype = "tmpfs"
        else:
            tree.mounts.pop(m.id, None)
            tree.events.append(("umount2", m.mountpoint, "MNT_DETACH"))
        masked.append(m.mountpoint)
    tree.events.append(("mount", rootfs, "/ (MS_MOVE)"))
    tree.events.append(("chroot", ".", ""))
    tree.events.append(("chdir", "/", ""))
    return masked


def set_readonly(tree, extra_flags=0):
    """setReadonly：直接 remount 失败就把 statfs 里的现有 flags 并上去再试。"""
    flags = MS_BIND | MS_REMOUNT | MS_RDONLY
    root = tree.current_root()
    if root.prop == MS_SHARED:
        return False
    root.prop = root.prop | (MS_RDONLY if extra_flags else 0)
    return True
