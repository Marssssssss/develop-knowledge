"""SemVer 2.0.0 解析与优先级比较(可执行)。零第三方依赖。

权威依据: semver.org 规范原文(仓库 semver/semver 的 semver.md, 本 demo 实读)。

核心事实(全部有原文支撑):
1. 正规版本号形如 ``X.Y.Z``,X/Y/Z 是非负整数且**不得有前导零**;
   ``1.9.0 -> 1.10.0`` 是**数值**递增, 不是字符串排序。
2. ``-`` 后的点分标识符是**预发布**;标识符只能是 ASCII 字母数字与连字符,
   不得为空, 且**数字标识符不得有前导零**。预发布版本优先级**低于**对应正规版本。
3. ``+`` 后是**构建元数据**;比较优先级时**必须忽略**它 —— 只差构建元数据的两个版本
   优先级相同(``1.0.0+a`` 与 ``1.0.0+b`` 不分先后)。
4. 优先级逐段左到右比较:先 major/minor/patch(**数值**)比;若相等,有预发布 < 无预发布;
   再逐段比预发布标识符 —— 纯数字**按数值**比、含字母或连字符**按 ASCII 字典序**比、
   **数字标识符恒低于非数字标识符**;前缀全相等时**标识符多的优先级更高**。
   官方例子: ``1.0.0-alpha < 1.0.0-alpha.1 < 1.0.0-alpha.beta < 1.0.0-beta
   < 1.0.0-beta.2 < 1.0.0-beta.11 < 1.0.0-rc.1 < 1.0.0``。
5. ``v1.2.3`` **不是**语义化版本(FAQ 明确): ``v`` 只是 tag 名的前缀习惯。
6. 已发布的版本其内容不得修改(与 OCI 的 digest 不可变语义同构)。
"""

from __future__ import annotations

import re

# 官方给出的「带编号捕获组」正则(ECMA/PCRE/Python/Go 通用), 原样照抄 semver.md。
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)


class SemverError(ValueError):
    """版本号不符合 SemVer 2.0.0(解析期错误, 不能静默降级)。"""


class Version:
    """一个合法的语义化版本。``build`` 不参与优先级比较。"""

    __slots__ = ("major", "minor", "patch", "prerelease", "build")

    def __init__(self, major, minor, patch, prerelease=(), build=()):
        self.major = major
        self.minor = minor
        self.patch = patch
        self.prerelease = tuple(prerelease)
        self.build = tuple(build)

    # ------------------------------------------------------------- 解析
    @classmethod
    def parse(cls, text: str) -> "Version":
        if not isinstance(text, str):
            raise SemverError("版本号必须是字符串")
        m = SEMVER_RE.match(text)
        if not m:
            raise SemverError("不是合法的 SemVer 2.0.0: %r" % text)
        major, minor, patch, pre, build = m.groups()
        return cls(int(major), int(minor), int(patch),
                   pre.split(".") if pre else (),
                   build.split(".") if build else ())

    # ------------------------------------------------------------- 输出
    @property
    def core(self) -> str:
        return "%d.%d.%d" % (self.major, self.minor, self.patch)

    def __str__(self) -> str:
        out = self.core
        if self.prerelease:
            out += "-" + ".".join(self.prerelease)
        if self.build:
            out += "+" + ".".join(self.build)
        return out

    def __repr__(self) -> str:
        return "Version(%s)" % self

    @property
    def is_prerelease(self) -> bool:
        return bool(self.prerelease)

    # ------------------------------------------------------------- 比较
    def compare(self, other: "Version") -> int:
        """按官方 11 条优先级规则返回 -1/0/1;构建元数据不参与。"""
        for a, b in ((self.major, other.major), (self.minor, other.minor),
                     (self.patch, other.patch)):
            if a != b:
                return 1 if a > b else -1
        if self.prerelease == other.prerelease:
            return 0
        # 有预发布的一方优先级更低
        if not self.prerelease:
            return 1
        if not other.prerelease:
            return -1
        for a, b in zip(self.prerelease, other.prerelease):
            c = _compare_identifier(a, b)
            if c:
                return c
        # 前缀全相等: 标识符更多的一方优先级更高
        if len(self.prerelease) == len(other.prerelease):
            return 0
        return 1 if len(self.prerelease) > len(other.prerelease) else -1

    def __eq__(self, other) -> bool:
        return isinstance(other, Version) and self.compare(other) == 0

    def __lt__(self, other) -> bool:
        return self.compare(other) < 0

    def __le__(self, other) -> bool:
        return self.compare(other) <= 0

    def __gt__(self, other) -> bool:
        return self.compare(other) > 0

    def __ge__(self, other) -> bool:
        return self.compare(other) >= 0

    def __hash__(self) -> int:
        # 与 __eq__ 保持一致: 只差构建元数据的版本必须同哈希
        return hash((self.major, self.minor, self.patch, self.prerelease))

    # ------------------------------------------------------------- 递增
    def bump(self, kind: str) -> "Version":
        """按官方递增规则产生下一个版本(递增时**清空**预发布与构建元数据)。"""
        if kind == "major":
            return Version(self.major + 1, 0, 0)
        if kind == "minor":
            return Version(self.major, self.minor + 1, 0)
        if kind == "patch":
            return Version(self.major, self.minor, self.patch + 1)
        if kind == "prerelease":
            # 预发布递增是本规范未规定的工程约定: 1.2.3 -> 1.2.4-0
            return Version(self.major, self.minor, self.patch + 1, ("0",))
        raise SemverError("未知的递增类型: %s" % kind)


def _compare_identifier(a: str, b: str) -> int:
    """官方规则: 纯数字按数值比; 数字恒低于非数字; 否则 ASCII 字典序。"""
    a_num = a.isdigit() and a.isascii()
    b_num = b.isdigit() and b.isascii()
    if a_num and b_num:
        ai, bi = int(a), int(b)
        return 0 if ai == bi else (1 if ai > bi else -1)
    if a_num:
        return -1
    if b_num:
        return 1
    return 0 if a == b else (1 if a > b else -1)


def sort_versions(texts) -> list:
    """把版本字符串按优先级升序排列(用插入排序展示比较次数以外的语义)。"""
    out = []
    for t in texts:
        v = Version.parse(t)
        pos = len(out)
        while pos > 0 and out[pos - 1].compare(v) > 0:
            pos -= 1
        out.insert(pos, v)
    return out


def highest(texts, include_prerelease: bool = True):
    """返回优先级最高的版本;``include_prerelease=False`` 时忽略预发布版本。"""
    best = None
    for t in texts:
        v = Version.parse(t)
        if v.is_prerelease and not include_prerelease:
            continue
        if best is None or v.compare(best) > 0:
            best = v
    return best
