#!/usr/bin/env python3
"""cgroup v2 层级 + eBPF 程序附加 + BPF token 委派的联合模型。

来源:
  cgroup v2  https://docs.kernel.org/admin-guide/cgroup-v2.html
  程序类型表  https://docs.kernel.org/bpf/libbpf/program_types.html
  BPF token  https://docs.ebpf.io/linux/concepts/token/
             https://docs.kernel.org/6.18/userspace-api/ebpf/syscall.html(BPF_TOKEN_CREATE)

被建模的规则(编号对应 README「原理详解」):
  H1 cgroup.subtree_control 只能启用 cgroup.controllers 里列出的控制器
  H2 一次写多个控制器是**全有或全无**
  H3 同一控制器重复出现时**最后一个生效**
  H4 自顶向下约束:子只能启用父已启用的控制器;子已启用时父不能禁用
  H5 no internal process:含进程的 cgroup 不能启用**域控制器**(根 cgroup 豁免)
  H6 在父上启用控制器会为**子**创建对应接口文件
  A1 libbpf 的 ELF section 名 -> (程序类型, attach 类型) 映射
  A2 只有 cgroup 类程序能附加到 cgroup
  A3 同一 (cgroup, attach_type) 上可挂多个程序,按挂载顺序执行
  T1 BPF token 从带委派挂载选项的 BPF FS 派生,委派集合四项:cmds/maps/progs/attachs
  T2 带 token 后能力检查用 **ns_capable(该 BPF FS 所属 userns)** 而非 init userns
  T3 token 不足以单独授权:进程仍须在该 userns 内具备对应能力
  T4 token 绑定创建它的 userns,跨 userns 使用无效
  T5 BPF_TOKEN_CREATE 自身要求在该 userns 内有 CAP_BPF

自检:python3 cgroup_bpf.py
"""
from __future__ import annotations

from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# A1:libbpf 的 ELF section 名表(取自官方 program_types 表,节选 cgroup 相关)
# --------------------------------------------------------------------------
SECTION_TABLE: dict[str, tuple[str, str]] = {
    "cgroup/dev":            ("BPF_PROG_TYPE_CGROUP_DEVICE",   "BPF_CGROUP_DEVICE"),
    "cgroup/skb":            ("BPF_PROG_TYPE_CGROUP_SKB",      "BPF_CGROUP_INET_INGRESS"),
    "cgroup_skb/ingress":    ("BPF_PROG_TYPE_CGROUP_SKB",      "BPF_CGROUP_INET_INGRESS"),
    "cgroup_skb/egress":     ("BPF_PROG_TYPE_CGROUP_SKB",      "BPF_CGROUP_INET_EGRESS"),
    "cgroup/getsockopt":     ("BPF_PROG_TYPE_CGROUP_SOCKOPT",  "BPF_CGROUP_GETSOCKOPT"),
    "cgroup/setsockopt":     ("BPF_PROG_TYPE_CGROUP_SOCKOPT",  "BPF_CGROUP_SETSOCKOPT"),
    "cgroup/connect4":       ("BPF_PROG_TYPE_CGROUP_SOCK_ADDR", "BPF_CGROUP_INET4_CONNECT"),
    "cgroup/connect6":       ("BPF_PROG_TYPE_CGROUP_SOCK_ADDR", "BPF_CGROUP_INET6_CONNECT"),
    "cgroup/sendmsg4":       ("BPF_PROG_TYPE_CGROUP_SOCK_ADDR", "BPF_CGROUP_UDP4_SENDMSG"),
    "cgroup/sendmsg6":       ("BPF_PROG_TYPE_CGROUP_SOCK_ADDR", "BPF_CGROUP_UDP6_SENDMSG"),
    "cgroup/post_bind4":     ("BPF_PROG_TYPE_CGROUP_SOCK",     "BPF_CGROUP_INET4_POST_BIND"),
    "cgroup/sock_create":    ("BPF_PROG_TYPE_CGROUP_SOCK",     "BPF_CGROUP_INET_SOCK_CREATE"),
    "cgroup/sock":           ("BPF_PROG_TYPE_CGROUP_SOCK",     "BPF_CGROUP_INET_SOCK_CREATE"),
    "cgroup/sysctl":         ("BPF_PROG_TYPE_CGROUP_SYSCTL",   "BPF_CGROUP_SYSCTL"),
    "sockops":               ("BPF_PROG_TYPE_SOCK_OPS",        "BPF_CGROUP_SOCK_OPS"),
}
# 非 cgroup 类(用于验证 A2:不能挂到 cgroup 上)
NON_CGROUP_SECTIONS = {"xdp", "tc", "classifier", "socket", "kprobe"}


class BpfError(Exception):
    def __init__(self, code: str, msg: str) -> None:
        super().__init__(f"{code}: {msg}")
        self.code = code


def resolve_section(section: str) -> tuple[str, str]:
    if section in NON_CGROUP_SECTIONS:
        raise BpfError("EINVAL", f"{section} 不是 cgroup 类程序,不能附加到 cgroup")
    if section not in SECTION_TABLE:
        raise BpfError("EINVAL", f"未知的 section 名 {section!r}")
    return SECTION_TABLE[section]


# --------------------------------------------------------------------------
# A3:cgroup 上的附加点
# --------------------------------------------------------------------------
@dataclass
class Cgroup:
    path: str
    procs: list[int] = field(default_factory=list)
    controllers: list[str] = field(default_factory=list)   # cgroup.controllers(只读)
    subtree: list[str] = field(default_factory=list)       # cgroup.subtree_control
    attached: dict[str, list[str]] = field(default_factory=dict)  # attach_type -> [prog]
    children: list["Cgroup"] = field(default_factory=list)


def attach_program(cg: Cgroup, section: str, prog_name: str) -> str:
    prog_type, attach_type = resolve_section(section)
    cg.attached.setdefault(attach_type, []).append(prog_name)
    return f"{prog_name}({prog_type}) -> {attach_type}"


def detach_program(cg: Cgroup, attach_type: str, prog_name: str) -> None:
    lst = cg.attached.get(attach_type, [])
    if prog_name not in lst:
        raise BpfError("ENOENT", f"{prog_name} 未挂在 {attach_type} 上")
    lst.remove(prog_name)


def write_subtree_control(cg: Cgroup, ops: str, is_root: bool = False) -> list[str]:
    """模拟 `echo '+cpu +memory -io' > cgroup.subtree_control`。

    H1 只能启用 cgroup.controllers 中列出的;
    H2 全有或全无;H3 重复时最后一个生效;
    H4 自顶向下约束(由调用方先检查父);
    H5 含进程时不能启用域控制器(根豁免)。
    """
    tokens = ops.split()
    if not tokens:
        raise BpfError("EINVAL", "空操作")
    # H3:同名控制器只保留最后一次
    final: dict[str, bool] = {}
    for t in tokens:
        if t[0] not in "+-":
            raise BpfError("EINVAL", f"操作必须以 + 或 - 开头:{t}")
        final[t[1:]] = t[0] == "+"

    # H1:只能启用 cgroup.controllers 里有的
    for name, enable in final.items():
        if enable and name not in cg.controllers:
            raise BpfError("EINVAL", f"{name} 不在 cgroup.controllers 中")
    # H5:含进程时不得启用域控制器
    domain_ctrls = {"cpu", "cpuset", "io", "memory", "pids", "hugetlb"}
    if not is_root and cg.procs:
        for name, enable in final.items():
            if enable and name in domain_ctrls:
                raise BpfError("EBUSY", f"cgroup 含 {len(cg.procs)} 个进程,不能启用域控制器 {name}")

    result = list(cg.subtree)
    for name, enable in final.items():
        if enable and name not in result:
            result.append(name)
        elif not enable and name in result:
            remove_controller(cg, name)          # H4:子仍在用时抛 EBUSY
            result.remove(name)
    cg.subtree = result
    # H6:在父上启用会为子创建接口文件
    for child in cg.children:
        child.controllers = sorted(set(child.controllers) | set(result))
    return result


def child_enabled(parent: Cgroup, name: str) -> bool:
    return any(name in c.subtree for c in parent.children)


def remove_controller(cg: Cgroup, name: str) -> None:
    """H4:一个控制器在任一子节点的 subtree_control 里仍启用时,父节点不能禁用它。"""
    if child_enabled(cg, name):
        raise BpfError("EBUSY", f"子节点仍启用 {name},父节点不能禁用")


# --------------------------------------------------------------------------
# T:BPF token
# --------------------------------------------------------------------------
@dataclass
class BpfToken:
    """从 BPF FS 派生的 token;委派集合为四个位集合。"""
    owning_userns: str
    delegate_cmds: set[str]
    delegate_maps: set[str]
    delegate_progs: set[str]
    delegate_attachs: set[str]


class Userns:
    def __init__(self, name: str, caps: set[str], is_init: bool = False) -> None:
        self.name = name
        self.caps = caps
        self.is_init = is_init


# 加载不同类别程序所需能力(官方 eBPF 文档:TC/XDP 需 CAP_BPF+CAP_NET_ADMIN;
# tracing 类需 CAP_BPF+CAP_PERFMON)
PROG_CAP_REQUIREMENT = {
    "BPF_PROG_TYPE_CGROUP_SKB": {("CAP_BPF", "CAP_NET_ADMIN")},
    "BPF_PROG_TYPE_CGROUP_SOCK_ADDR": {("CAP_BPF", "CAP_NET_ADMIN")},
    "BPF_PROG_TYPE_CGROUP_DEVICE": {("CAP_BPF", "CAP_SYS_ADMIN")},
    "BPF_PROG_TYPE_CGROUP_SYSCTL": {("CAP_BPF", "CAP_SYS_ADMIN")},
    "BPF_PROG_TYPE_KPROBE": {("CAP_BPF", "CAP_PERFMON")},
}

TOKEN_DELEGATION_KEYS = {"delegate_cmds", "delegate_maps", "delegate_progs", "delegate_attachs"}


def create_token(fs_owning_userns: str, fs_delegated: dict[str, set[str]],
                 creator: Userns, cmd: str = "BPF_TOKEN_CREATE") -> BpfToken:
    """T5:BPF_TOKEN_CREATE 要求在该 BPF FS 所属 userns 内有 CAP_BPF。"""
    if set(fs_delegated) - TOKEN_DELEGATION_KEYS:
        raise BpfError("EINVAL", "未知的委派选项")
    if cmd not in fs_delegated.get("delegate_cmds", set()) | {"BPF_TOKEN_CREATE"}:
        raise BpfError("EPERM", f"{cmd} 未被委派")
    if "CAP_BPF" not in creator.caps:
        raise BpfError("EPERM", "BPF_TOKEN_CREATE 需要 ns_capable(CAP_BPF)")
    return BpfToken(fs_owning_userns,
                    set(fs_delegated.get("delegate_cmds", set())),
                    set(fs_delegated.get("delegate_maps", set())),
                    set(fs_delegated.get("delegate_progs", set())),
                    set(fs_delegated.get("delegate_attachs", set())))


def can_load(prog_type: str, caller_userns: Userns, token: BpfToken | None) -> bool:
    """T2/T3/T4:判定能否加载该类型程序。

    无 token 时,能力用 `capable()` 检查,即必须落在 **init userns**;
    有 token 时改用 `ns_capable()`(该 BPF FS 所属 userns),但**仍要求**进程在该 userns 内有能力。
    """
    req = PROG_CAP_REQUIREMENT.get(prog_type)
    if req is None:
        return False
    need = next(iter(req))
    if token is None:
        return caller_userns.is_init and set(need) <= caller_userns.caps   # T2
    if token.owning_userns != caller_userns.name:
        return False                                    # T4:跨 userns 无效
    if prog_type not in token.delegate_progs:
        return False                                    # T1:未委派该程序类型
    if "CAP_BPF" not in caller_userns.caps:
        return False                                    # T3:token 不足以单独授权
    return set(need) <= caller_userns.caps
