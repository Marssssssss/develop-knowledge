#!/usr/bin/env python3
"""cgroup v2 + eBPF 附加模型的自检脚本(模型本体见 cgroup_bpf.py)。

运行:python3 cgroup_check.py
"""
from cgroup_bpf import (
    SECTION_TABLE, NON_CGROUP_SECTIONS, BpfError, Cgroup, Userns, BpfToken,
    resolve_section, attach_program, detach_program, write_subtree_control,
    child_enabled, create_token, can_load,
)


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


def expect_error(label: str, code: str, fn, *a, **kw) -> None:
    try:
        fn(*a, **kw)
        check(label, False, "没有报错")
    except BpfError as exc:
        check(label, exc.code == code, str(exc))


def main() -> int:
    print("=== 1. ELF section 名 -> (程序类型, attach 类型)(A1) ===")
    cases = [
        ("cgroup_skb/ingress", "BPF_PROG_TYPE_CGROUP_SKB", "BPF_CGROUP_INET_INGRESS"),
        ("cgroup_skb/egress", "BPF_PROG_TYPE_CGROUP_SKB", "BPF_CGROUP_INET_EGRESS"),
        ("cgroup/connect4", "BPF_PROG_TYPE_CGROUP_SOCK_ADDR", "BPF_CGROUP_INET4_CONNECT"),
        ("cgroup/dev", "BPF_PROG_TYPE_CGROUP_DEVICE", "BPF_CGROUP_DEVICE"),
        ("cgroup/sysctl", "BPF_PROG_TYPE_CGROUP_SYSCTL", "BPF_CGROUP_SYSCTL"),
        ("sockops", "BPF_PROG_TYPE_SOCK_OPS", "BPF_CGROUP_SOCK_OPS"),
    ]
    for sec, want_t, want_a in cases:
        got = resolve_section(sec)
        check(f"{sec}", got == (want_t, want_a), f"{got[0]} / {got[1]}")
    check("cgroup/skb 与 cgroup_skb/ingress 等价(表中 attach 类型相同)",
          resolve_section("cgroup/skb") == resolve_section("cgroup_skb/ingress"))
    expect_error("未知 section -> EINVAL", "EINVAL", resolve_section, "cgroup/nope")
    expect_error("非 cgroup 程序不能挂到 cgroup(A2) -> EINVAL", "EINVAL",
                 resolve_section, "xdp")

    print("\n=== 2. 同一附加点上挂多个程序(A3) ===")
    cg = Cgroup("/sys/fs/cgroup/demo", procs=[4242])
    print("  " + attach_program(cg, "cgroup_skb/ingress", "prog_a"))
    print("  " + attach_program(cg, "cgroup_skb/ingress", "prog_b"))
    check("两个程序按挂载顺序排在同一个 attach 点上",
          cg.attached["BPF_CGROUP_INET_INGRESS"] == ["prog_a", "prog_b"],
          str(cg.attached["BPF_CGROUP_INET_INGRESS"]))
    detach_program(cg, "BPF_CGROUP_INET_INGRESS", "prog_a")
    check("detach 后只剩 prog_b", cg.attached["BPF_CGROUP_INET_INGRESS"] == ["prog_b"])
    expect_error("detach 不存在的程序 -> ENOENT", "ENOENT",
                 detach_program, cg, "BPF_CGROUP_INET_INGRESS", "prog_x")

    print("\n=== 3. subtree_control 的三条规则(H1/H2/H3) ===")
    root = Cgroup("/sys/fs/cgroup", controllers=["cpu", "io", "memory", "pids"])
    leaf = Cgroup("/sys/fs/cgroup/leaf")
    root.children = [leaf]
    write_subtree_control(root, "+memory +pids", is_root=True)
    check("启用后 cgroup.subtree_control = memory pids", root.subtree == ["memory", "pids"])
    check("子节点自动获得接口文件(H6)", leaf.controllers == ["memory", "pids"],
          str(leaf.controllers))
    expect_error("启用未列在 cgroup.controllers 的控制器 -> EINVAL", "EINVAL",
                 write_subtree_control, root, "+cpuset", True)
    expect_error("一次写多个控制器时全有或全无(H2) -> EINVAL", "EINVAL",
                 write_subtree_control, root, "+cpu +nope", True)
    check("H2 失败后状态未被部分修改", root.subtree == ["memory", "pids"], str(root.subtree))
    before = list(root.subtree)
    write_subtree_control(root, "+cpu -cpu", is_root=True)
    check("重复控制器最后一个生效(H3):净效果为不启用",
          "cpu" not in root.subtree, f"{before} -> {root.subtree}")

    print("\n=== 4. 自顶向下约束(H4) ===")
    parent = Cgroup("/sys/fs/cgroup/a", procs=[], controllers=["cpu", "memory"])
    child = Cgroup("/sys/fs/cgroup/a/b", controllers=["cpu", "memory"])
    parent.children = [child]
    check("父尚未启用 cpu", "cpu" not in parent.subtree)
    check("子尚未启用 cpu(故此时父可以自由操作)",
          child_enabled(parent, "cpu") is False)
    parent.subtree = ["cpu"]
    child.subtree = ["cpu"]      # 子启用后,父不能再禁用
    check("子已启用 cpu", child_enabled(parent, "cpu") is True)
    expect_error("子仍在用时父禁用 cpu -> EBUSY", "EBUSY",
                 write_subtree_control, parent, "-cpu", True)
    child.subtree = []
    check("子先禁用后,父可以禁用 cpu",
          write_subtree_control(parent, "-cpu", True) == [])

    print("\n=== 5. no internal process(H5) ===")
    busy = Cgroup("/sys/fs/cgroup/busy", procs=[1, 2, 3],
                  controllers=["cpu", "memory", "pids"])
    expect_error("含进程时启用域控制器 memory -> EBUSY", "EBUSY",
                 write_subtree_control, busy, "+memory")
    busy.procs.clear()
    check("把进程迁走后可以启用",
          write_subtree_control(busy, "+memory") == ["memory"])
    root2 = Cgroup("/sys/fs/cgroup", procs=[99], controllers=["cpu", "memory"])
    write_subtree_control(root2, "+cpu", is_root=True)
    check("根 cgroup 豁免该约束(它本就含进程)", root2.subtree == ["cpu"])

    print("\n=== 6. BPF token 的委派与授权(T1-T5) ===")
    userns = Userns("userns-of-container", caps={"CAP_BPF"})
    delegated = {
        "delegate_cmds": {"BPF_PROG_LOAD"},
        "delegate_maps": {"BPF_MAP_TYPE_ARRAY"},
        "delegate_progs": {"BPF_PROG_TYPE_CGROUP_SKB"},
        "delegate_attachs": {"BPF_CGROUP_INET_INGRESS"},
    }
    tok = create_token("userns-of-container", delegated, userns)
    check("四项委派集合都被 token 继承",
          tok.delegate_cmds == {"BPF_PROG_LOAD"} and
          tok.delegate_attachs == {"BPF_CGROUP_INET_INGRESS"})
    expect_error("缺 CAP_BPF 时创建 token -> EPERM(T5)", "EPERM",
                 create_token, "userns", delegated, Userns("u", set()))
    expect_error("未知委派选项 -> EINVAL", "EINVAL",
                 create_token, "userns", {"delegate_nope": set()}, userns)

    net_userns = Userns("userns-of-container", caps={"CAP_BPF", "CAP_NET_ADMIN"})
    check("有 token + 委派 + 能力 -> 可加载 cgroup_skb",
          can_load("BPF_PROG_TYPE_CGROUP_SKB", net_userns, tok))
    check("有 token 但程序类型未委派 -> 拒绝(T1)",
          not can_load("BPF_PROG_TYPE_CGROUP_DEVICE", net_userns, tok))
    other = Userns("another-userns", caps={"CAP_BPF", "CAP_NET_ADMIN"})
    check("token 跨 userns 使用 -> 拒绝(T4)",
          not can_load("BPF_PROG_TYPE_CGROUP_SKB", other, tok))
    only_bpf = Userns("userns-of-container", caps={"CAP_BPF"})
    check("token 不足以单独授权:缺 CAP_NET_ADMIN -> 拒绝(T3)",
          not can_load("BPF_PROG_TYPE_CGROUP_SKB", only_bpf, tok))
    init_like = Userns("init-userns", caps={"CAP_BPF", "CAP_NET_ADMIN", "CAP_PERFMON"},
                       is_init=True)
    check("无 token 时能力检查落在 init userns,故在容器 userns 里不成立(T2)",
          not can_load("BPF_PROG_TYPE_CGROUP_SKB", net_userns, None))
    check("同样的能力放在 init userns 里则成立(对照)",
          can_load("BPF_PROG_TYPE_CGROUP_SKB", init_like, None))
    check("tracing 类需要 CAP_BPF+CAP_PERFMON,不是 CAP_NET_ADMIN",
          not can_load("BPF_PROG_TYPE_KPROBE", net_userns, None))
    perf = Userns("init-userns", caps={"CAP_BPF", "CAP_PERFMON"}, is_init=True)
    check("CAP_BPF+CAP_PERFMON 可加载 kprobe", can_load("BPF_PROG_TYPE_KPROBE", perf, None))

    print(f"\n合计:{OK} passed / {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
