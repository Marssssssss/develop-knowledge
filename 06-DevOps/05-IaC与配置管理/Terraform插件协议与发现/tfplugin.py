# -*- coding: utf-8 -*-
"""Terraform 插件协议、发现与版本选择模型。

口径全部来自实际读过的官方原文（见 README 参考资料）：
  - Terraform plugin protocol：版本化接口、主版本划兼容性、次版本可加、protobuf + gRPC
  - Protocol v6 兼容 Terraform CLI 1.0+，v5 兼容 0.12+
  - How Terraform Works With Plugins：Core/Plugin 分工、init 发现流程、版本选择三条规则、lock file
  - Provider Requirements：`~>` 允许「最右侧分量递增」；lock file 存在时一律遵守
  - go-plugin：握手（magic cookie + protocol version）、子进程隔离、仅本地可靠网络
"""
from __future__ import annotations

GOOD_RPCS = ["GetProviderSchema", "ConfigureProvider", "ValidateResourceTypeConfig",
             "ReadResource", "PlanResourceChange", "ApplyResourceChange",
             "ImportResourceState", "ReadDataSource"]


def parse_version(s: str):
    return tuple(int(x) for x in s.split("."))


def _upper_of_pessimistic(parts):
    """`~>` 允许最右侧分量递增：~>1.0.4 → <1.1.0；~>1.0 → <2.0。"""
    if len(parts) < 2:
        return None
    up = list(parts[:-1])
    up[-1] += 1
    return tuple(up)


def satisfies(ver: str, constraint: str) -> bool:
    """支持 = != > >= < <= ~>，逗号分隔表示多条都要满足。"""
    v = parse_version(ver)
    for raw in [c for c in constraint.split(",") if c.strip()]:
        c = raw.strip()
        if c.startswith("~>"):
            low = parse_version(c[2:].strip())
            up = _upper_of_pessimistic(low)
            if v < low:
                return False
            if up is not None and v >= up:
                return False
            continue
        for op in ("!=", ">=", "<=", "=", ">", "<"):
            if c.startswith(op):
                o = parse_version(c[len(op):].strip())
                ok = {"=": v == o, "!=": v != o, ">": v > o, ">=": v >= o,
                      "<": v < o, "<=": v <= o}[op]
                if not ok:
                    return False
                break
        else:
            raise ValueError("无法识别的约束: %r" % c)
    return True


def cli_protocol_majors(cli_version: str) -> set:
    """v6 兼容 CLI 1.0+，v5 兼容 0.12+。"""
    v = parse_version(cli_version)
    majors = set()
    if v >= (0, 12):
        majors.add(5)
    if v >= (1, 0):
        majors.add(6)
    return majors


def negotiate(cli_max: tuple, plugin_protos):
    """协商出 (major, minor)。

    「主版本划分兼容性」→ 取两侧都能用的最大主版本；
    「次版本是叠加的」→ 有效次版本取双方较小者（本 demo 的口径，README 已标注）。
    无共同主版本 → 报错（go-plugin：给出 human friendly error）。
    """
    # 插件协议只有 v5/v6 两个主版本，CLI 会同时使用它能支持的全部主版本
    cli_majors = {5}
    if cli_max[0] >= 6:
        cli_majors.add(6)
    common = cli_majors & {p[0] for p in plugin_protos}
    if not common:
        raise ValueError("incompatible protocol version: CLI 支持 %s，插件提供 %s"
                         % (sorted(cli_majors), sorted(p[0] for p in plugin_protos)))
    major = max(common)
    cli_minor = cli_max[1] if len(cli_max) > 1 else 0
    plug_minor = max(p[1] for p in plugin_protos if p[0] == major)
    return major, min(cli_minor, plug_minor)


def select_version(constraint, installed, registry, lock=None):
    """返回 (version, source)；source ∈ locked/installed/registry/failed。

    官方三条规则：
      1) 有 lock file 且满足约束 → 一律遵守 lock（"will all obey it"）
      2) 否则，已安装里有可接受的 → 用**已安装里最新的**，即使 registry 有更新的可接受版本
      3) 都没有且 registry 有 → 从 registry 下载最新的可接受版本
      4) 都没有 → init 失败，需要手工安装
    registry 条目为 (version, protocol_major)，协议不兼容的候选会被过滤
    """
    if lock is not None and satisfies(lock, constraint):
        return lock, "locked"

    def acceptable(cands):
        return sorted([c for c in cands if satisfies(c, constraint)], key=parse_version)

    ins = acceptable(installed)
    if ins:
        return ins[-1], "installed"
    reg = acceptable([v for v, _ in registry])
    if reg:
        return reg[-1], "registry"
    return None, "failed"


def select_with_protocol(constraint, installed, registry, cli_version, lock=None):
    """registry 发现时把「协议版本」当作额外兼容性元数据参与筛选（官方原文口径）。"""
    cli_m = cli_protocol_majors(cli_version)
    visible = [(v, p) for v, p in registry if p in cli_m]
    return select_version(constraint, installed, visible, lock)


def handshake(plugin_stdout: str, cookie_key: str, cookie_value: str,
              plugin_proto: int, host_proto: int):
    """go-plugin 握手：先校验 magic cookie，再校验 protocol version。

    cookie 校验是为了防止「把一个普通二进制当插件启动」—— 原文：
    "A very basic protocol version is supported that can be incremented to
     invalidate any previous plugins... When a protocol version is incompatible,
     a human friendly error message is shown to the end user."
    """
    line = "%s=%s" % (cookie_key, cookie_value)
    if line not in plugin_stdout.splitlines():
        return False, "magic cookie 不匹配：这不是一个能被本宿主识别的插件"
    if plugin_proto != host_proto:
        return False, "incompatible protocol version: 插件 %d，宿主 %d" % (plugin_proto, host_proto)
    return True, "ok"
