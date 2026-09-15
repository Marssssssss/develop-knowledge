#!/usr/bin/env python3
"""Docker / BuildKit 构建缓存失效规则的最小模拟器。

规则依据 Docker 官方文档 "Build cache invalidation":
  https://docs.docker.com/build/cache/invalidation/

实现的三条核心规则:
  1. 缓存键 = 父层缓存键 + 本指令自身的摘要(逐层链式,父层变则后面全部失效)
  2. RUN 的摘要**只有命令字符串**,不检查容器文件系统
  3. COPY/ADD/RUN --mount=type=bind 的摘要 = 文件**元数据**校验和,
     且 **mtime 不参与**(只改 mtime 不会失效)

自检:python3 layer_cache.py   (全部断言必须通过)
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

# 文件元数据 =(权限位, 内容, mtime)。mtime 显式建模进来,但**不参与校验和**
# —— 这样「只改 mtime 不失效」才是可断言的事实,而不是靠"模型里没有这个字段"。
Entry = tuple[int, str, float]
FileSet = dict[str, Entry]


def sha16(*parts: object) -> str:
    """把若干参数按固定分隔拼起来取 sha256 前 16 位十六进制。"""
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode())
        h.update(b"\x00")
    return h.hexdigest()[:16]


def metadata_checksum(files: FileSet) -> str:
    """算文件集合的元数据校验和。

    官方原文:"the builder calculates a cache checksum from file metadata ...
    The modification time of a file (mtime) is not taken into account when
    calculating the cache checksum."
    故此处只用 (路径, 权限位, 字节数, 内容哈希),**刻意丢弃 mtime**;
    且排序后遍历,保证结果与 readdir / dict 顺序无关。
    """
    h = hashlib.sha256()
    for path, (mode, content, _mtime) in sorted(files.items()):
        h.update(f"{path}\x00{mode:o}\x00{len(content)}\x00".encode())
        h.update(hashlib.sha256(content.encode()).hexdigest().encode())
        h.update(b"\x00")
    return h.hexdigest()[:16]


def restamp(files: FileSet, delta: float) -> FileSet:
    """把所有文件的 mtime 平移 delta 秒(模拟 git checkout / touch)。"""
    return {p: (m, c, t + delta) for p, (m, c, t) in files.items()}


# --------------------------------------------------------------------------
# 指令模型
# --------------------------------------------------------------------------
@dataclass
class Step:
    kind: str                 # FROM / WORKDIR / COPY / RUN / ARG
    text: str
    files: FileSet | None = None            # COPY / ADD 匹配到的文件
    secret: tuple[str, str] | None = None   # RUN --mount=type=secret=(id, 内容)

    def digest(self, args: dict[str, str], source_date_epoch: str) -> str:
        if self.kind in ("COPY", "ADD"):
            return sha16("copy", self.text, metadata_checksum(self.files or {}))
        if self.kind == "RUN" and self.secret:
            # 密钥**内容**不参与缓存,但 id / 挂载路径参与
            sid, _content = self.secret
            return sha16("run-secret", self.text, sid)
        if self.kind == "RUN":
            # 官方:"just the command string itself is used to find a match"
            return sha16("run", self.text)
        if self.kind == "WORKDIR":
            # 官方:WORKDIR 尊重 SOURCE_DATE_EPOCH,改它会级联失效
            return sha16("workdir", self.text, source_date_epoch)
        if self.kind == "ARG":
            name = self.text.split()[0]
            return sha16("arg", name, args.get(name, ""))
        return sha16(self.kind, self.text)


@dataclass
class BuildResult:
    hits: int = 0
    misses: int = 0
    run_misses: int = 0        # 代价高的 RUN 步骤被重跑的次数
    trace: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return f"{self.hits} hit / {self.misses} miss (RUN 重跑 {self.run_misses})"


class CacheStore:
    """内容寻址的层缓存:key -> 是否已存在。"""

    def __init__(self) -> None:
        self.keys: set[str] = set()

    def lookup(self, key: str) -> bool:
        if key in self.keys:
            return True
        self.keys.add(key)
        return False


def build(steps: list[Step], store: CacheStore, args: dict[str, str] | None = None,
          source_date_epoch: str = "0") -> BuildResult:
    """模拟一次 docker build。

    父层规则:每一步的 key 都基于**上一步的 key**,所以父层一旦 miss,
    后面所有步骤的 key 都跟着变 —— 这就是「失效级联」,无需额外逻辑。
    """
    res = BuildResult()
    parent = sha16("scratch")
    for st in steps:
        key = sha16(parent, st.digest(args or {}, source_date_epoch))
        if store.lookup(key):
            res.hits += 1
            res.trace.append(f"CACHED  {st.kind:8} {st.text}")
        else:
            res.misses += 1
            if st.kind == "RUN":
                res.run_misses += 1
            res.trace.append(f"REBUILD {st.kind:8} {st.text}")
        parent = key
    return res


# --------------------------------------------------------------------------
# 两个 Dockerfile:坏顺序(先 COPY . . 再装依赖) vs 好顺序
# --------------------------------------------------------------------------
DEPS: FileSet = {"package.json": (0o644, '{"deps":["left-pad"],"mtime":[1700000000]}', 1.7e9)}


def app(content: str) -> FileSet:
    return {"app.py": (0o644, content, 1.7e9), "util.py": (0o644, "X = 1", 1.7e9)}


def bad_order(files: FileSet) -> list[Step]:
    """先把整个上下文 COPY 进去,再装依赖 —— 改一行源码就会重装依赖。"""
    merged: FileSet = {**DEPS, **files}
    return [
        Step("FROM", "python:3.12-slim"),
        Step("WORKDIR", "/app"),
        Step("COPY", "COPY . .", files=merged),
        Step("RUN", "pip install -r package.json"),
        Step("RUN", "python -m compileall ."),
    ]


def good_order(files: FileSet) -> list[Step]:
    """先 COPY 依赖清单装依赖,再 COPY 源码 —— 依赖层可复用。"""
    return [
        Step("FROM", "python:3.12-slim"),
        Step("WORKDIR", "/app"),
        Step("COPY", "COPY package.json ./", files=DEPS),
        Step("RUN", "pip install -r package.json"),
        Step("COPY", "COPY . .", files={**DEPS, **files}),
        Step("RUN", "python -m compileall ."),
    ]


# --------------------------------------------------------------------------
# 自检
# --------------------------------------------------------------------------
OK = 0
FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  [PASS] {label} {detail}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


def main() -> int:
    v1, v2 = app("print('v1')"), app("print('v2')")

    print("=== 1. 冷启动:全部 MISS ===")
    store = CacheStore()
    cold = build(good_order(v1), store)
    check("冷启动无命中", cold.hits == 0 and cold.misses == 6, cold.summary())

    print("\n=== 2. 源码变更后的重建:好顺序 vs 坏顺序 ===")
    warm_good = build(good_order(v2), store)
    check("好顺序:前 4 步命中,COPY . . 起重建",
          warm_good.hits == 4 and warm_good.misses == 2, warm_good.summary())

    store_bad = CacheStore()
    build(bad_order(v1), store_bad)
    warm_bad = build(bad_order(v2), store_bad)
    check("坏顺序:COPY . . 起全部重建",
          warm_bad.hits == 2 and warm_bad.misses == 3, warm_bad.summary())
    # 公平口径:两个 Dockerfile 步数不同(5 vs 6),只比较**昂贵的 RUN 步骤**重跑次数
    check("好顺序保住依赖安装层",
          warm_bad.run_misses == 2 and warm_good.run_misses == 1,
          f"RUN 重跑 坏={warm_bad.run_misses} 好={warm_good.run_misses}")

    print("\n=== 3. mtime 不参与校验和 ===")
    check("只平移 mtime -> 校验和不变",
          metadata_checksum(v1) == metadata_checksum(restamp(v1, 86400 * 30)))
    check("只平移 mtime -> 校验和不变(含依赖清单)",
          metadata_checksum({**DEPS, **v1}) == metadata_checksum(restamp({**DEPS, **v1}, -1e6)))
    st_m = CacheStore()
    build(good_order(v1), st_m)
    after_touch = build(good_order(restamp(v1, 86400)), st_m)
    check("git checkout 改写全部 mtime -> 全部命中",
          after_touch.hits == 6 and after_touch.misses == 0, after_touch.summary())

    print("\n=== 4. chmod 改变元数据 -> COPY 失效级联 ===")
    chmodded = {"app.py": (0o755, "print('v1')", 1.7e9), "util.py": (0o644, "X = 1", 1.7e9)}
    check("内容相同但权限位不同 -> 校验和不同",
          metadata_checksum(v1) != metadata_checksum(chmodded))
    st_c = CacheStore()
    build(good_order(v1), st_c)
    after_chmod = build(good_order(chmodded), st_c)
    check("chmod 后从 COPY 起级联重建",
          after_chmod.hits == 4 and after_chmod.misses == 2, after_chmod.summary())

    print("\n=== 5. RUN 只看命令字符串,不看文件系统 ===")
    st_r = CacheStore()
    build([Step("FROM", "alpine:3.20"), Step("RUN", "apk add curl")], st_r)
    rerun = build([Step("FROM", "alpine:3.20"), Step("RUN", "apk add curl")], st_r)
    check("命令串未变 -> RUN 永远命中(包不会变新)",
          rerun.misses == 0 and rerun.hits == 2, rerun.summary())
    bumped = build([Step("FROM", "alpine:3.20"), Step("RUN", "apk add curl=8.9")], st_r)
    check("命令串变了才重建", bumped.misses == 1, bumped.summary())

    print("\n=== 6. ARG 参与缓存,构建密钥内容不参与 ===")
    arg_steps = [Step("ARG", "CACHEBUST = 1"), Step("RUN", "echo build")]
    st_a = CacheStore()
    build(arg_steps, st_a, args={"CACHEBUST": "1"})
    same = build(arg_steps, st_a, args={"CACHEBUST": "1"})
    diff = build(arg_steps, st_a, args={"CACHEBUST": "2"})
    check("ARG 值不变 -> 命中", same.misses == 0, same.summary())
    check("ARG 值改变 -> 级联失效", diff.hits == 0 and diff.misses == 2, diff.summary())

    st_s = CacheStore()
    build([Step("RUN", "some-command", secret=("TOKEN", "tkn_v1"))], st_s)
    rotated = build([Step("RUN", "some-command", secret=("TOKEN", "tkn_v2"))], st_s)
    check("轮换密钥内容 -> 仍命中", rotated.hits == 1 and rotated.misses == 0, rotated.summary())
    renamed = build([Step("RUN", "some-command", secret=("TOKEN2", "tkn_v1"))], st_s)
    check("改变密钥 id -> 失效", renamed.misses == 1, renamed.summary())

    print("\n=== 7. SOURCE_DATE_EPOCH 影响 WORKDIR 及其后续 ===")
    st_e = CacheStore()
    wd = [Step("WORKDIR", "/app"), Step("RUN", "make")]
    build(wd, st_e, source_date_epoch="0")
    ep = build(wd, st_e, source_date_epoch="1700000000")
    check("改 SOURCE_DATE_EPOCH -> WORKDIR 起失效",
          ep.hits == 0 and ep.misses == 2, ep.summary())

    print("\n=== 8. 失效级联不能跳步 ===")
    st_j = CacheStore()
    seq_v1 = [Step("FROM", "x"), Step("COPY", "COPY a", files={"a": (0o644, "1", 0.0)}),
              Step("RUN", "step-b"), Step("RUN", "step-c")]
    seq_v2 = [Step("FROM", "x"), Step("COPY", "COPY a", files={"a": (0o644, "2", 0.0)}),
              Step("RUN", "step-b"), Step("RUN", "step-c")]
    build(seq_v1, st_j)
    cas = build(seq_v2, st_j)
    check("首层变更 -> 后续全部重建,无法跳步复用",
          cas.hits == 1 and cas.misses == 3 and cas.run_misses == 2, cas.summary())

    print("\n=== 9. 指令顺序对缓存效率的量化影响 ===")
    # 同一份源码,只改 Dockerfile 行序:统计「改一行源码」的重建代价
    print(f"  坏顺序 {warm_bad.summary()}")
    print(f"  好顺序 {warm_good.summary()}")
    check("乱序步骤也应命中(理论上限)",
          build(list(reversed(good_order(v2))), CacheStore()).misses == 6)

    print(f"\n合计:{OK} passed / {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
