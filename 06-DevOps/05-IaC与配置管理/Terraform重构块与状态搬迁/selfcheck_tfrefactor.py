# -*- coding: utf-8 -*-
"""tfrefactor 自检：全部基于官方原文口径（见 README 参考资料）。"""
from tfrefactor import (Addr, parse_addr, Move, apply_moves, auto_count_move,
                        desired_addresses, plan)

N = 0
FAIL = []


def check(label, cond, detail=""):
    global N
    N += 1
    if not cond:
        FAIL.append("%s  %s" % (label, detail))


def acts(p):
    return dict(p)


# ---- 1. 地址解析 ----
a = parse_addr("module.a[2].aws_instance.example")
check("A1 module 带键", str(a) == "module.a[2].aws_instance.example", str(a))
check("A2 模块解析", a.modules == [("a", "2")] and a.rtype == "aws_instance", str(a.modules))
b = parse_addr('aws_instance.a["small"]')
check("A3 引号键", b.key == "small" and b.rname == "a", repr(b.key))
c = parse_addr('aws_instance.web["a.b"]')
check("A4 键内点号不误切", c.key == "a.b" and c.rtype == "aws_instance", repr(c.key))
d = parse_addr("data.aws_ami.x")
check("A5 data 资源", d.is_data and d.rtype == "aws_ami" and d.rname == "x", str(d))
check("A6 整资源无键", not parse_addr("aws_instance.a").has_any_key)
check("A7 模块无键但有资源键", parse_addr("module.a.aws_instance.b[0]").has_any_key)

# ---- 2. 整资源级 moved：两侧都不带键 → 覆盖全部实例 ----
st = {"aws_instance.a[0]": {"t": "m3"}, "aws_instance.a[1]": {"t": "m3"}}
cfg = {"aws_instance.b": {"mode": "count", "n": 2, "attrs": {"t": "m3"}}}
mv = [Move("aws_instance.a", "aws_instance.b")]
check("B1 整资源级判定", not mv[0].instance_level)
check("B2 无 moved 时是销毁+新建",
      acts(plan(st, cfg)) == {"aws_instance.b[0]": "create", "aws_instance.b[1]": "create",
                              "aws_instance.a[0]": "destroy", "aws_instance.a[1]": "destroy"},
      acts(plan(st, cfg)))
p = acts(plan(st, cfg, moves=mv))
check("B3 moved 后不销毁", "destroy" not in p.values(), p)
check("B4 实例键被保留", p == {"aws_instance.b[0]": "noop", "aws_instance.b[1]": "noop"}, p)

# ---- 3. 新实例没有旧对象时忽略 moved ----
p = acts(plan({}, {"aws_instance.b": {"mode": "count", "n": 2, "attrs": {}}}, moves=mv))
check("C1 空 state 走 create", p == {"aws_instance.b[0]": "create", "aws_instance.b[1]": "create"}, p)

# ---- 4. 实例级 moved：至少一侧带键 ----
m2 = Move("aws_instance.a", 'aws_instance.a["small"]')
check("D1 单侧带键即实例级", m2.instance_level)
r = apply_moves({"aws_instance.a": {"t": "m3"}}, [m2])
check("D2 单实例→键", list(r) == ['aws_instance.a["small"]'], list(r))

m3 = Move("aws_instance.d[2]", "aws_instance.d")
r = apply_moves({"aws_instance.d[2]": {}}, [m3])
check("D3 键→单实例", list(r) == ["aws_instance.d"], list(r))

st = {"aws_instance.c[0]": {"t": "m3"}, "aws_instance.c[1]": {"t": "m3"}}
r = apply_moves(st, [Move("aws_instance.c[0]", 'aws_instance.c["small"]'),
                     Move("aws_instance.c[1]", 'aws_instance.c["tiny"]')])
check("D4 count→for_each 逐个映射",
      sorted(r) == ['aws_instance.c["small"]', 'aws_instance.c["tiny"]'], sorted(r))

# 实例级不会「顺带」搬走同资源的其它实例
r = apply_moves(st, [Move("aws_instance.c[0]", 'aws_instance.c["small"]')])
check("D5 实例级不误伤兄弟实例", sorted(r) == ['aws_instance.c["small"]', 'aws_instance.c[1]'], sorted(r))

# ---- 5. 模块改名 / 拆分 ----
r = apply_moves({"module.a.aws_instance.example": {}}, [Move("module.a", "module.b")])
check("E1 模块改名加前缀", list(r) == ["module.b.aws_instance.example"], list(r))
st = {"aws_instance.a": {}, "aws_instance.b": {}, "aws_instance.c": {}}
r = apply_moves(st, [Move("aws_instance.a", "module.x.aws_instance.a"),
                     Move("aws_instance.b", "module.x.aws_instance.b"),
                     Move("aws_instance.c", "module.y.aws_instance.c")])
check("E2 拆模块三块", sorted(r) == ["module.x.aws_instance.a", "module.x.aws_instance.b",
                                    "module.y.aws_instance.c"], sorted(r))
r = apply_moves({"aws_instance.example": {}},
                [Move("aws_instance.example", "module.new[2].aws_instance.example")])
check("E3 带键模块也算实例级并整体搬迁",
      list(r) == ["module.new[2].aws_instance.example"], list(r))

# ---- 6. move 链 ----
chain = [Move("aws_instance.a", "aws_instance.b"), Move("aws_instance.b", "aws_instance.c")]
r1 = apply_moves({"aws_instance.a": {}}, chain)
r2 = apply_moves({"aws_instance.b": {}}, chain)
check("F1 链首也能直达终点", list(r1) == ["aws_instance.c"], list(r1))
check("F2 链中间同样可达终点", list(r2) == ["aws_instance.c"], list(r2))

# ---- 7. 给单实例资源加 count 的自动搬迁 ----
st = {"aws_instance.a": {"t": "m3"}}
cfg = {"aws_instance.a": {"mode": "count", "n": 2, "attrs": {"t": "m3"}}}
p = acts(plan(st, cfg))
check("G1 加 count 自动搬 0 号", p == {"aws_instance.a[0]": "noop", "aws_instance.a[1]": "create"}, p)
p = acts(plan(st, cfg, moves=[Move("aws_instance.a", "aws_instance.a[0]")]))
check("G2 显式 moved 提及该资源后不再自动搬",
      p == {"aws_instance.a[0]": "noop", "aws_instance.a[1]": "create"}, p)
p = acts(plan(st, {"aws_instance.a": {"mode": "for_each", "keys": ["small"],
                                      "attrs": {"t": "m3"}}}))
check("G3 for_each 无自动搬迁→旧对象被销毁",
      p == {'aws_instance.a["small"]': "create", "aws_instance.a": "destroy"}, p)

# ---- 8. removed 块 ----
st = {"aws_instance.a": {}}
cfg = {}
p = acts(plan(st, cfg, removed=[{"from": "aws_instance.a"}]))
check("H1 removed 默认 destroy", p == {"aws_instance.a": "destroy"}, p)
p = acts(plan(st, cfg, removed=[{"from": "aws_instance.a", "destroy": False}]))
check("H2 destroy=false 只移出 state", p == {"aws_instance.a": "forget"}, p)
p = acts(plan(st, cfg))
check("H3 无 removed 时残留照常 destroy", p == {"aws_instance.a": "destroy"}, p)

# ---- 9. import 块 ----
cfg = {"aws_instance.a": {"attrs": {}}}
p = acts(plan({}, cfg, imports=[{"to": "aws_instance.a", "id": "i-123"}]))
check("I1 有 import 走 import 而非 create", p == {"aws_instance.a": "import"}, p)
try:
    plan({}, cfg, imports=[{"to": "aws_instance.a", "id": "i", "identity": {"x": "y"}}])
    check("I2 id/identity 互斥", False, "未抛错")
except ValueError:
    check("I2 id/identity 互斥", True)
try:
    plan({}, cfg, imports=[{"to": "aws_instance.zzz", "id": "i"}])
    check("I3 to 必须是已有 resource 块", False, "未抛错")
except ValueError:
    check("I3 to 必须是已有 resource 块", True)

# ---- 10. 其它 ----
try:
    Move("aws_instance.a", "data.aws_instance.a")
    check("J1 不能搬到 data 资源", False, "未抛错")
except ValueError:
    check("J1 不能搬到 data 资源", True)
p = acts(plan({"aws_instance.a": {"t": "m1"}},
              {"aws_instance.a": {"attrs": {"t": "m2"}}}))
check("J2 属性变化判 update", p == {"aws_instance.a": "update"}, p)
check("J3 count 展开顺序",
      [a_ for a_, _ in desired_addresses({"aws_instance.a": {"mode": "count", "n": 3}})]
      == ["aws_instance.a[0]", "aws_instance.a[1]", "aws_instance.a[2]"])
check("J4 整资源 moved 不改模块",
      list(apply_moves({"module.a.aws_instance.x[0]": {}},
                       [Move("module.a.aws_instance.x", "module.a.aws_instance.y")]))
      == ["module.a.aws_instance.y[0]"])

print("checks=%d fail=%d" % (N, len(FAIL)))
for f in FAIL:
    print("  FAIL", f)
