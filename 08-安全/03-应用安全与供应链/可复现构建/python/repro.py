"""可复现构建（Reproducible Builds）的阻力与归一化模型。

依据 reproducible-builds.org 的规范与文档：
- SOURCE_DATE_EPOCH 规范（https://reproducible-builds.org/specs/source-date-epoch/）：
  值的格式、必须用于嵌入时间戳、"时间戳钳制"、不得对子进程 unset、畸形值应非零退出
- Timestamps 页：无法让工具支持时的后处理（strip-nondeterminism / libfaketime 的坑）
- Build path 页：`-fdebug-prefix-map` / `-fmacro-prefix-map` / `-ffile-prefix-map`
  分别处理调试信息与 `__FILE__` 宏带来的路径泄漏

模型口径：
- 归档条目只保留会影响字节的字段（名字 / mtime / uid / gid / uname / gname / mode / 内容）。
- en_US.UTF-8 的排序规则用一个"忽略大小写与标点"的键近似 glibc collation 的第一遍，
  仅用于说明"区域设置会改变顺序"这件事，不是完整实现。
"""

import hashlib
import time

# ZIP 的时间戳下界：1980-01-01 00:00:00 UTC（规范文档里 Python zipfile 示例的常量）
ZIP_EPOCH_MIN = 315532800


class SourceDateEpochError(ValueError):
    """SOURCE_DATE_EPOCH 畸形（规范：构建进程 SHOULD 以非零码退出）。"""


def parse_source_date_epoch(raw):
    """按规范文档的 C 参考实现解析：纯十进制、无前后垃圾、不溢出。"""
    # 严格按参考实现：不做 trim，任何非数字字符都算 Trailing garbage
    text = raw
    if not text:
        raise SourceDateEpochError("No digits were found")
    digits = ""
    for ch in text:
        if not ("0" <= ch <= "9"):
            raise SourceDateEpochError("Trailing garbage: %s" % text[text.index(ch):])
        digits += ch
    value = int(digits)
    # 参考实现按 unsigned long 上界拒绝；这里用 64 位上界
    if value > 0xFFFFFFFFFFFFFFFF:
        raise SourceDateEpochError("value must be smaller than or equal to %d" %
                                   0xFFFFFFFFFFFFFFFF)
    return value


def build_time(env, wall_clock):
    """规范：构建进程"当前时间"一律用 SOURCE_DATE_EPOCH 代替。"""
    raw = env.get("SOURCE_DATE_EPOCH")
    if raw is None or raw == "":
        return wall_clock
    return parse_source_date_epoch(raw)


def inherit_env(env):
    """规范：构建进程 MUST NOT 对子进程 unset 这个变量（已经存在就不能摘掉）。"""
    return dict(env)


def clamp_mtime(mtime, sde):
    """时间戳钳制：比 SOURCE_DATE_EPOCH 新的全部改写成 SOURCE_DATE_EPOCH。

    规范原文：rewriting timestamps more recent than SOURCE_DATE_EPOCH back to the latter
    （对应 GNU tar 的 --clamp-mtime）。因此是**上界**钳制，不是下界。
    """
    if sde is None:
        return mtime
    return mtime if mtime <= sde else sde


def zip_datetime(mtime, sde):
    """ZIP 存不下 1980 年以前的时间，所以要先取一次下界。"""
    return max(ZIP_EPOCH_MIN, clamp_mtime(mtime, sde))


def format_date(mtime, tz_offset):
    """%Y-%m-%d 的格式化受 TZ 影响 —— 同一时刻不同时区可能落到不同日期。"""
    return time.strftime("%Y-%m-%d", time.gmtime(mtime + tz_offset))


def locale_key(name, collation):
    """排序键：C 区域按字节；en_US.UTF-8 近似为"忽略大小写与标点"。"""
    if collation == "C":
        return name
    lowered = name.lower()
    return "".join(ch for ch in lowered if ch.isalnum())


def sort_names(names, collation):
    return sorted(names, key=lambda n: locale_key(n, collation))


class Entry:
    """归档里的一个条目。"""

    def __init__(self, name, data="", mtime=0, mode=0o644, uid=0, gid=0,
                 uname="", gname=""):
        self.name = name
        self.data = data
        self.mtime = mtime
        self.mode = mode
        self.uid = uid
        self.gid = gid
        self.uname = uname
        self.gname = gname

    def copy(self):
        return Entry(self.name, self.data, self.mtime, self.mode,
                     self.uid, self.gid, self.uname, self.gname)


def apply_umask(mode, umask):
    """umask 会把位清掉，构建者的 umask 不同产物就不同。"""
    return mode & ~umask


def normalize(entries, env, build_path=None, normalized_path="/build"):
    """归一化管线：钳制时间戳、清零属主、重映射构建路径、稳定排序。"""
    sde = build_time(env, None)
    out = []
    for e in entries:
        n = e.copy()
        n.mtime = clamp_mtime(n.mtime, sde)
        n.uid = 0
        n.gid = 0
        n.uname = ""
        n.gname = ""
        if build_path and n.name.startswith(build_path):
            n.name = normalized_path + n.name[len(build_path):]
        if build_path and build_path in n.data:
            n.data = n.data.replace(build_path, normalized_path)
        out.append(n)
    # 目录遍历顺序不稳定，必须显式排序；排序又受区域设置影响，所以强制 C
    out.sort(key=lambda e: e.name.encode("utf-8"))
    return out


def digest(entries):
    """把条目序列压成一个摘要（真实场景就是产物字节的哈希）。"""
    h = hashlib.sha256()
    for e in entries:
        h.update(e.name.encode("utf-8"))
        h.update(b"\x00")
        h.update(e.data.encode("utf-8"))
        h.update(b"\x00")
        h.update(str(e.mtime).encode("utf-8"))
        h.update(str(e.mode & 0o777).encode("utf-8"))
        h.update(str(e.uid).encode("utf-8"))
        h.update(str(e.gid).encode("utf-8"))
        h.update(e.uname.encode("utf-8"))
        h.update(e.gname.encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


def build(entries, env, umask=0o022, build_path=None, collation="C", normalize_it=True):
    """模拟一次构建：返回产物摘要。"""
    material = []
    names = sort_names([e.name for e in entries], collation)
    by_name = {e.name: e for e in entries}
    for name in names:
        e = by_name[name].copy()
        e.mode = apply_umask(e.mode, umask)
        material.append(e)
    if normalize_it:
        material = normalize(material, env, build_path=build_path)
    return digest(material)
