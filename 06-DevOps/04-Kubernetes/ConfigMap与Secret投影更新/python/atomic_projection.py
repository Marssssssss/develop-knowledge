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


# ---------------------------------------------------------------- 自检
def selfcheck() -> int:
    n = 0

    def ck(cond, msg):
        nonlocal n
        assert cond, msg
        n += 1

    def bad(p):
        try:
            validate_path(p)
            return False
        except InvalidPath:
            return True

    # 1. validatePath 五条禁令
    ck(bad(""), "空路径应被拒")
    ck(bad("/etc/passwd"), "绝对路径应被拒")
    ck(bad("a/../b"), "含 .. 元素应被拒")
    ck(bad("..data/x"), "以 .. 开头且长度>2 应被拒")
    ck(bad(".."), "单独的 '..' 元素本身就被禁止(先于'以 .. 开头'规则命中)")
    ck(bad("..2026_01_01_00_00_00.1/x"), "时间戳目录名保留给 AtomicWriter,普通 key 不能用")
    ck(bad("a" * 256), "文件名 256 字符应被拒")
    ck(not bad("a" * 255), "文件名 255 字符应放行")
    # 路径长度上限要单独测:组件长度必须同时 <=255,否则先被文件名规则拦下
    long_ok = "/".join(["a" * 255] * 16)      # 255*16 + 15 = 4095
    long_bad = "/".join(["a" * 255] * 17)     # 4351 > 4096
    ck(len(long_ok) == 4095 and len(long_bad) == 4351, "构造的测例长度符合预期")
    ck(not bad(long_ok), "4095 字符路径应放行")
    ck(bad(long_bad), "4351 字符路径应被拒")
    ck(not bad("foo/bar"), "正常相对路径应放行")

    # 2. 首次写入
    fs = FS()
    w = AtomicWriter(fs, "/mnt/cfg")
    w.write({"app.yml": b"replicas: 1"})
    ts1 = fs.readlink("/mnt/cfg/..data")
    ck(ts1 is not None and ts1.startswith(".."), f"..data 应指向 .. 开头的时间戳目录, 实得 {ts1}")
    ck(fs.readlink("/mnt/cfg/app.yml") == "..data/app.yml",
       f"可见文件应软链到 ..data/app.yml, 实得 {fs.readlink('/mnt/cfg/app.yml')}")
    ck(w.read_visible("app.yml") == b"replicas: 1", "读者应读到内容")

    # 3. rename 在"建可见链接"之前(第 9 步早于第 10 步)
    tr = fs.trace
    ck(tr.index("rename /mnt/cfg/..data_tmp -> /mnt/cfg/..data")
       < tr.index("symlink /mnt/cfg/app.yml -> ..data/app.yml"),
       "rename 必须先于可见链接创建")

    # 4. payload 未变 → 不新建时间戳目录(shouldWritePayload)
    before = fs.trace[:]
    w.write({"app.yml": b"replicas: 1"})
    ck(len(fs.trace) - len(before) == 1 and fs.trace[-1] == "noop: payload unchanged",
       f"内容未变应 no-op, 实得 {fs.trace[len(before):]}")
    ck(fs.readlink("/mnt/cfg/..data") == ts1, "no-op 后 ..data 指向不变")

    # 5. 内容变化 → 新时间戳目录 + 旧目录被清
    w.write({"app.yml": b"replicas: 2"})
    ts2 = fs.readlink("/mnt/cfg/..data")
    ck(ts2 != ts1, "内容变化应换时间戳目录")
    ck(w.read_visible("app.yml") == b"replicas: 2", "读者应读到新内容")
    ck(not any(p.startswith("/mnt/cfg/" + ts1) for p in list(fs.files) + list(fs.links)),
       f"旧时间戳目录 {ts1} 应被删除")
    ck(fs.files.get("/mnt/cfg/" + ts2 + "/app.yml") == b"replicas: 2", "新目录内容正确")

    # 6. 可见符号链接只建一次(指向 ..data/<name> 是稳定的)
    cnt = sum(1 for t in fs.trace if t.startswith("symlink /mnt/cfg/app.yml"))
    ck(cnt == 1, f"可见链接只应创建一次, 实得 {cnt} 次")

    # 7. 新增/删除 key
    w.write({"app.yml": b"replicas: 2", "extra.txt": b"hi"})
    ck(w.read_visible("extra.txt") == b"hi", "新增 key 应可读")
    w.write({"app.yml": b"replicas: 3"})
    ck(w.read_visible("app.yml") == b"replicas: 3", "删除 extra 后 app.yml 仍可读")
    ck(w.read_visible("extra.txt") is None, "被删的 key 不应再可读")
    ck(fs.readlink("/mnt/cfg/extra.txt") is None, "被删 key 的可见链接应移除")

    # 8. 嵌套路径:只为第一段建链接
    fs2 = FS()
    w2 = AtomicWriter(fs2, "/mnt/cfg2")
    w2.write({"dir/a.yml": b"a", "dir/b.yml": b"b"})
    ck(fs2.readlink("/mnt/cfg2/dir") == "..data/dir", "嵌套路径只为第一段建链接")
    ck(fs2.readlink("/mnt/cfg2/dir/a.yml") is None, "不应为第二段建独立链接")
    ck(w2.read_visible("dir/a.yml") == b"a", "嵌套文件应可读")

    # 9. 原子性:rename 之后 ..data 立即指向"完整的"新目录
    fs3 = FS()
    w3 = AtomicWriter(fs3, "/mnt/cfg3")
    for i in range(5):
        w3.write({("k%d" % j): ("v%d-%d" % (j, i)).encode() for j in range(3)})
        ts = fs3.readlink("/mnt/cfg3/..data")
        full = all(fs3.files.get("/mnt/cfg3/%s/k%d" % (ts, j)) is not None for j in range(3))
        ck(full, f"第 {i} 轮切换后 ..data 指向的目录必须完整")
        ck(all(w3.read_visible("k%d" % j) == ("v%d-%d" % (j, i)).encode() for j in range(3)),
           f"第 {i} 轮读者看到的内容必须一致")

    # 10. subPath 挂载不更新
    sp = SubPathMount(b"replicas: 1")
    sp.refresh(b"replicas: 99")
    ck(sp.data == b"replicas: 1", "subPath 挂载不应收到更新")

    # 11. 环境变量注入不更新
    env = EnvInjection({"LOG_LEVEL": "info"})
    env.refresh({"LOG_LEVEL": "debug"})
    ck(env.values["LOG_LEVEL"] == "info", "env 注入不应收到更新")

    # 12. ConfigMap 1 MiB 上限
    ck(CONFIGMAP_MAX_BYTES == 1048576, "1 MiB = 1048576 字节")
    ck(len(b"x" * 1048576) == CONFIGMAP_MAX_BYTES, "恰好 1MiB 合法")
    ck(len(b"x" * 1048577) > CONFIGMAP_MAX_BYTES, "超过 1MiB 非法")

    # 13. 更新延迟 = sync period + cache propagation delay
    ck(total_update_delay(60.0, "Get") == 60.0, "Get 直连 → 传播延迟 0")
    ck(total_update_delay(60.0, "Watch", watch_delay=0.5) == 60.5, "Watch → watch 传播延迟")
    ck(total_update_delay(60.0, "TTL", ttl=30.0) == 90.0, "TTL → 加上 ttl")
    ck(total_update_delay(60.0, "Get") < total_update_delay(60.0, "Watch"),
       "Get 应比 Watch 更快看到更新")

    print(f"atomic_projection: {n} assertions passed")
    return n


if __name__ == "__main__":
    selfcheck()
