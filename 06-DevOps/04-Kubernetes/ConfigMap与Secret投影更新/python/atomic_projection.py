"""
ConfigMap / Secret 卷投影的原子写入 + kubelet 侧的三条"不更新"规则。

权威来源(实际读过):
  1. https://kubernetes.io/docs/concepts/configuration/configmap/
  2. https://cdn.jsdelivr.net/gh/kubernetes/kubernetes@master/pkg/volume/util/atomic_writer.go

从源码抄下来的事实:
  - AtomicWriter 保留所有以 `..` 开头的路径名;`..data` 是指向"时间戳目录"的符号链接,
    可见文件是指向 `..data/<name>` 的符号链接 —— **更新 = 换一个符号链接的指向**,rename 是原子的。
  - validatePath 五条禁令(原文注释):
      1. 不能是绝对路径      2. 不能含 `..` 元素
      3. 不能以 `..` 开头     4. 文件名不能超过 255 字符
      5. 路径不能超过 4096 字符
  - shouldWritePayload:逐个比对 oldTsDir 下的文件,**全都没变就直接返回,不新建时间戳目录**。
  - newTimestampDir 用 `..2006_01_02_15_04_05.` 前缀 + MkdirTemp 的随机后缀。
  - createUserVisibleFiles 只为 **路径的第一段** 建符号链接,且 **只在链接不存在时** 才建
    (因为 `xxx -> ..data/xxx` 这个指向是稳定的,内容换了它也不用换)。
  - Write 的 12 步里,第 9 步 rename 在第 10 步建可见链接 **之前** —— 顺序反了就会露出现半成品。

从文档抄下来的事实:
  - "A ConfigMap ... cannot exceed 1 MiB."
  - "the total delay ... can be as long as the kubelet sync period + cache propagation delay,
    where the cache propagation delay depends on the chosen cache type
    (it equals to watch propagation delay, ttl of cache, or zero correspondingly)."
    缓存类型由 KubeletConfiguration 的 configMapAndSecretChangeDetectionStrategy 决定:
    Watch(默认) / TTL / Get(直连 apiserver)。
  - "ConfigMaps consumed as environment variables are not updated automatically and require a pod restart."
  - "A container using a ConfigMap as a subPath volume mount will not receive ConfigMap updates."
"""
from typing import Dict, List, Optional, Tuple

MAX_PATH_LENGTH = 4096
MAX_FILE_NAME_LENGTH = 255
DATA_DIR_NAME = "..data"
NEW_DATA_DIR_NAME = "..data_tmp"
CONFIGMAP_MAX_BYTES = 1024 * 1024          # 1 MiB


class InvalidPath(Exception):
    pass


def validate_path(target_path: str) -> None:
    """逐条对应 validatePath 的 5 条禁令。"""
    if target_path == "":
        raise InvalidPath("invalid path: must be relative path")
    if target_path.startswith("/"):
        raise InvalidPath("invalid path: must be relative path: %s" % target_path)
    if len(target_path) > MAX_PATH_LENGTH:
        raise InvalidPath("invalid path: must be less than or equal to %d characters"
                          % MAX_PATH_LENGTH)
    items = target_path.split("/")
    for it in items:
        if it == "..":
            raise InvalidPath("invalid path: must not contain '..': %s" % target_path)
        if len(it) > MAX_FILE_NAME_LENGTH:
            raise InvalidPath("invalid path: filenames must be less than or equal to %d characters"
                              % MAX_FILE_NAME_LENGTH)
    if items[0].startswith("..") and len(items[0]) > 2:
        raise InvalidPath("invalid path: must not start with '..': %s" % target_path)


# ---------------------------------------------------------------- 内存文件系统
class FS:
    """够用的内存 FS:文件 / 符号链接 / 目录,外加操作轨迹。"""

    def __init__(self):
        self.files: Dict[str, bytes] = {}
        self.links: Dict[str, str] = {}
        self.dirs = set([""])
        self.trace: List[str] = []

    def _parent_dirs(self, path: str) -> None:
        parts = path.split("/")[:-1]
        cur = ""
        for p in parts:
            cur = p if cur == "" else cur + "/" + p
            self.dirs.add(cur)

    def write_file(self, path: str, data: bytes) -> None:
        self._parent_dirs(path)
        self.files[path] = data
        self.trace.append("write " + path)

    def symlink(self, target: str, link_path: str) -> None:
        self._parent_dirs(link_path)
        self.links[link_path] = target
        self.trace.append("symlink %s -> %s" % (link_path, target))

    def rename(self, old: str, new: str) -> None:
        if old in self.links:
            self.links[new] = self.links.pop(old)
        elif old in self.files:
            self.files[new] = self.files.pop(old)
        else:
            raise KeyError("rename source missing: " + old)
        self.trace.append("rename %s -> %s" % (old, new))

    def readlink(self, path: str) -> Optional[str]:
        return self.links.get(path)

    def remove(self, path: str) -> None:
        self.files.pop(path, None)
        self.links.pop(path, None)
        self.trace.append("remove " + path)

    def remove_tree(self, prefix: str) -> None:
        for p in [p for p in list(self.files) + list(self.links) if p.startswith(prefix + "/")]:
            self.remove(p)

    def exists(self, path: str) -> bool:
        return path in self.files or path in self.links or path in self.dirs


# ---------------------------------------------------------------- AtomicWriter
class AtomicWriter:
    def __init__(self, fs: FS, target_dir: str, clock=None):
        self.fs = fs
        self.target = target_dir
        self.clock = clock if clock is not None else (lambda i: "..2026_09_19_16_40_05.%08d" % i)
        self._ts_counter = 0
        self.perms_calls: List[str] = []

    def _p(self, rel: str) -> str:
        return rel if self.target == "" else self.target + "/" + rel

    def new_timestamp_dir(self) -> str:
        self._ts_counter += 1
        name = self.clock(self._ts_counter)
        self.fs.dirs.add(self._p(name))
        self.fs.trace.append("mkdir " + self._p(name))
        return name

    def _should_write(self, payload: Dict[str, bytes], old_ts: Optional[str]) -> bool:
        """对应 shouldWritePayload:全都没变就返回 False。"""
        if old_ts is None:
            return True
        for name, data in payload.items():
            cur = self.fs.files.get(self._p(old_ts + "/" + name))
            if cur != data:
                return True
        return False

    def write(self, payload: Dict[str, bytes],
              set_perms=None) -> None:
        # (1) 校验
        for name in payload:
            validate_path(name)
        # (2) 读 ..data 找到当前时间戳目录
        old_ts = self.fs.readlink(self._p(DATA_DIR_NAME))
        # (3)(4) 是否需要写
        if not self._should_write(payload, old_ts):
            self.fs.trace.append("noop: payload unchanged")
            return
        # (5) 新时间戳目录
        new_ts = self.new_timestamp_dir()
        # (6) 写 payload
        for name, data in payload.items():
            self.fs.write_file(self._p(new_ts + "/" + name), data)
        # (7) 权限
        if set_perms is not None:
            set_perms(new_ts)
            self.perms_calls.append(new_ts)
            self.fs.trace.append("setPerms " + new_ts)
        # (8) ..data_tmp -> new_ts
        self.fs.symlink(new_ts, self._p(NEW_DATA_DIR_NAME))
        # (9) rename,原子
        self.fs.rename(self._p(NEW_DATA_DIR_NAME), self._p(DATA_DIR_NAME))
        # (10) 建可见符号链接(只为第一段,且仅当不存在)
        for name in payload:
            first = name.split("/")[0]
            if self.fs.readlink(self._p(first)) is None and not self.fs.files.get(self._p(first)):
                self.fs.symlink(DATA_DIR_NAME + "/" + first, self._p(first))
        # (11) 删除不再出现的可见路径
        visible = {n.split("/")[0] for n in payload}
        for p in list(self.fs.links):
            rel = p[len(self.target) + 1:] if self.target else p
            if "/" not in rel and rel.startswith("..") is False and rel not in visible:
                self.fs.remove(p)
        # (12) 删除旧时间戳目录
        if old_ts is not None:
            self.fs.remove_tree(self._p(old_ts))

    # --- 读者视角 ---
    def read_visible(self, name: str) -> Optional[bytes]:
        first = name.split("/")[0]
        if self.fs.readlink(self._p(first)) is None:
            return None                       # key 已被移除,可见链接不存在
        ts = self.fs.readlink(self._p(DATA_DIR_NAME))
        assert ts is not None, "..data missing"
        return self.fs.files.get(self._p(ts + "/" + name))


# ---------------------------------------------------------------- kubelet 侧
def total_update_delay(sync_period: float, strategy: str, ttl: float = 60.0,
                       watch_delay: float = 0.5) -> float:
    """kubelet sync period + cache propagation delay。

    strategy: Watch(默认) / TTL / Get(直连 apiserver,延迟 0)。
    """
    if strategy == "Watch":
        prop = watch_delay
    elif strategy == "TTL":
        prop = ttl
    elif strategy == "Get":
        prop = 0.0
    else:
        raise ValueError("unknown strategy: " + strategy)
    return sync_period + prop


class SubPathMount:
    """subPath 挂载:挂载时把字节拷进去,之后永不更新(文档原文)。"""

    def __init__(self, data: bytes):
        self.data = data        # 快照

    def refresh(self, new_data: bytes) -> None:
        pass                    # 故意什么都不做


class EnvInjection:
    """envFrom / valueFrom:容器启动时注入,之后不更新(文档原文)。"""

    def __init__(self, values: Dict[str, str]):
        self.values = dict(values)

    def refresh(self, new_values: Dict[str, str]) -> None:
        pass




if __name__ == "__main__":
    from selfcheck_atomic_projection import selfcheck
    selfcheck()
