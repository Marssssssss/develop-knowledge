#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ghidra 脚本化批量重命名:计划 → 预演 → 事务提交。

依据(本轮实读的官方文档):
  * Ghidra 自带一个 `BatchRename.java` 脚本用于**模式化批量改名**——官方 Symbol Management
    文档在 "Batch Renaming" 一节写明:『Rename multiple symbols: Use BatchRename.java
    script / Pattern-based renaming / Regular expression support / Bulk operations』,
    流程是『1 Select Symbols → 2 Apply Pattern → Define naming pattern → Preview changes
    → Apply to selection』。本 demo 把这条流程抽象成 plan / dry-run / apply 三段。
  * 只该动**自动生成的名字**。官方文档列出自动名:『Auto-Generated Symbols: Ghidra creates
    default symbols: FUN_ for functions, DAT_ for data, LAB_ for labels, SUB_ for
    subroutines』,并建议『Replace auto-generated names with meaningful names』。
    用户已命名的符号来源是 USER_DEFINED,优先级最高,批量脚本不该碰。
  * 冲突处理用官方的 makeUnique 口径:『if the name is a duplicate, the address will be
    concatenated to name to make it unique』。
  * 所有写操作必须在事务里,失败整体回滚(官方脚本示例一律
    currentProgram.startTransaction / endTransaction(txId, true|false))。
  * 脚本参数来自 GhidraScript.getScriptArgs();注意 askXxx() 交互方法会**先**从参数里取,
    剩下的才归 getScriptArgs(),所以非交互脚本必须按固定顺序解析。

运行: python rename_batch.py     退出码 0 表示全部断言通过。
"""

import re
import sys

from ghidra_symbols import (NamingError, Program, Transaction, is_default_name,
                            unique_name)

FAIL = []


def check(cond, label, detail=""):
    if not cond:
        FAIL.append(label)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", label,
                           ("  <- " + str(detail)) if detail else ""))
    return cond


# ---------------------------------------------------------------- 参数

ARGS_USAGE = "dry|run <include-regex> <template> [exclude-regex]"


def parse_args(argv):
    """模型对应 getScriptArgs()。顺序固定:非交互脚本不能依赖 askXxx 的顺序提示。"""
    if len(argv) < 3:
        raise ValueError("用法: %s" % ARGS_USAGE)
    mode, include, template = argv[0], argv[1], argv[2]
    if mode not in ("dry", "run"):
        raise ValueError("mode 必须是 dry 或 run,收到 %r" % mode)
    exclude = argv[3] if len(argv) > 3 else None
    return dict(mode=mode, include=re.compile(include), template=template, exclude=exclude)


# ---------------------------------------------------------------- 计划

def build_plan(program, include, template, exclude=None, only_default=True):
    """生成改名计划。返回 [(addr, old, new), ...],按地址升序 —— 顺序确定才好复现。

    template 里 `\\1` 等反向引用来自正则捕获组;`{addr}` 会被替换成 8 位十六进制地址。
    """
    plan = []
    for sym in program.all_symbols():
        if not sym.primary:
            continue                                  # 只管每个地址的主符号
        if only_default and not is_default_name(sym.name):
            continue                                  # 用户命名的符号绝不碰
        if exclude is not None and re.search(exclude, sym.name):
            continue
        if not include.search(sym.name):
            continue
        try:
            # 用 re.sub 而不是 m.expand():只替换命中部分、保留未命中的前后缀,
            # 与 Go 的 ReplaceAllString 语义一致,两种语言的结果才能逐条对照。
            new = include.sub(template, sym.name).replace("{addr}", "%08x" % sym.addr)
        except re.error as e:
            raise ValueError("模板 %r 与包含正则的捕获组不匹配: %s" % (template, e))
        if new == sym.name:
            continue                                  # 名没变就不进计划,保证幂等
        plan.append((sym.addr, sym.name, new))
    return sorted(plan)


def resolve_conflicts(program, plan):
    """目标名已被**别的地址**占用 → 按官方 makeUnique 拼地址后缀。"""
    taken = {}
    for sym in program.all_symbols():
        if sym.primary:
            taken[sym.name] = sym.addr
    fixed = []
    for addr, old, new in plan:
        if taken.get(new) not in (None, addr):
            new = unique_name(new, addr)
        taken[new] = addr
        fixed.append((addr, old, new))
    return fixed


# ---------------------------------------------------------------- 执行

def apply_plan(program, plan, dry_run=True):
    """dry_run=True 只报告;否则在**单个事务**里执行,任一失败整体回滚。"""
    report = dict(planned=len(plan), changed=0, failed=0, rolled_back=False,
                  entries=[])
    if dry_run:
        for addr, old, new in plan:
            report["entries"].append((addr, old, new))
        return report
    tx = Transaction(program, "batch rename").begin()
    try:
        for addr, old, new in plan:
            ok = program.rename_symbol(addr, old, new, "USER_DEFINED")
            if ok:
                report["changed"] += 1
                report["entries"].append((addr, old, new))
            else:
                report["failed"] += 1
    except (NamingError, Exception) as e:            # 任一异常 → 整批回滚
        tx.rollback()
        report["rolled_back"] = True
        report["changed"] = 0
        report["entries"] = []
        report["error"] = "%s: %s" % (type(e).__name__, e)
        return report
    tx.commit()
    return report


# ---------------------------------------------------------------- 自检

def sample_program():
    p = Program()
    p.create_function(0x401000, "FUN_00401000")
    p.create_function(0x401100, "FUN_00401100")
    p.create_function(0x401200, "FUN_00401200")
    p.create_label(0x402000, "LAB_00402000", True, "DEFAULT")
    p.create_label(0x403000, "config_loader", True, "USER_DEFINED")   # 用户命名,不许动
    p.create_label(0x404000, "FUN_00404000_marker", True, "DEFAULT")
    return p


def main():
    print("== 1. 参数解析(getScriptArgs 顺序固定) ==")
    a = parse_args(["dry", r"^FUN_[0-9A-Fa-f]+$", "sub_{addr}"])
    check(a["mode"] == "dry" and a["include"].pattern.startswith("^FUN_"),
          "解析 dry 模式 + 包含正则 + 模板")
    for bad in ([], ["go", "x", "y"], ["dry"]):
        try:
            parse_args(bad)
            check(False, "参数 %r 应报错" % (bad,))
        except ValueError:
            check(True, "参数 %r 被拒绝(用法: %s)" % (bad, ARGS_USAGE))

    print("== 2. 计划:只碰自动生成名 ==")
    p = sample_program()
    plan = build_plan(p, re.compile(r"^FUN_[0-9A-Fa-f]+$"), "sub_{addr}")
    addrs = [a for a, _, _ in plan]
    check(addrs == [0x401000, 0x401100, 0x401200],
          "3 个 FUN_ 进计划,LAB_ 不匹配包含正则", ["%x" % x for x in addrs])
    check(0x403000 not in addrs, "用户命名的 config_loader 被跳过(USER_DEFINED 优先级最高)")
    check(0x404000 not in addrs, "FUN_00404000_marker 也不匹配 ^FUN_[0-9A-Fa-f]+$ 的尾部约束")
    plan_g = build_plan(p, re.compile(r"^FUN_0040(1[0-9A-Fa-f]{3})$"), r"func_\1")
    check([x[2] for x in plan_g] == ["func_1000", "func_1100", "func_1200"],
          r"模板里的 \1 反向引用正则捕获组(注意 Go 里写作 $1)",
          [x[2] for x in plan_g])
    try:
        build_plan(p, re.compile(r"^FUN_(00401000)$"), r"x_\2")
        check(False, "引用不存在的捕获组应当报错")
    except ValueError as e:
        check(True, "引用不存在的捕获组 → 报错而不是静默产出怪名字", str(e)[:34] + "...")

    print("== 3. dry-run 不修改程序 ==")
    before = p.dump()
    rep = apply_plan(p, plan, dry_run=True)
    check(p.dump() == before, "预演后程序完全未变")
    check(rep["planned"] == 3 and rep["changed"] == 0,
          "预演只报告 planned=3 / changed=0", (rep["planned"], rep["changed"]))

    print("== 4. 提交改名 ==")
    rep = apply_plan(p, plan, dry_run=False)
    check(rep["changed"] == 3 and not rep["rolled_back"], "3 条全部改名成功", rep["changed"])
    names = sorted(f.name for f in p.functions())
    check(names == ["sub_00401000", "sub_00401100", "sub_00401200"],
          "函数名已更新({addr} 展开为 8 位十六进制,与 FUN_ 的默认风格一致)", names)

    print("== 5. 幂等 + 为什么新名字要写成大写前缀 ==")
    plan2 = build_plan(p, re.compile(r"^FUN_[0-9A-Fa-f]+$"), "sub_{addr}")
    check(plan2 == [], "旧正则再也匹配不到任何符号 → 计划为空")
    plan3 = build_plan(p, re.compile(r"^sub_"), "fun_{addr}")
    check(plan3 == [],
          "改了名之后连 ^sub_ 也拿不到东西:小写 sub_ 不被认作自动生成名(前缀判断区分大小写)")
    plan3b = build_plan(p, re.compile(r"^sub_"), "fun_{addr}", only_default=False)
    check(len(plan3b) == 3,
          "只有显式关掉 only_default 才会再拿到 3 条 —— 这正是保护用户命名的机制",
          len(plan3b))
    check(build_plan(p, re.compile(r"^sub_"), "sub_{addr}") == [] and
          build_plan(p, re.compile(r"^sub_"), "SUB_{addr}") == [],
          "新旧名同名或只换大小写时计划为空(名字没变就不进计划),保证可反复执行")

    print("== 6. 冲突:目标名已被别的地址占用 ==")
    p2 = Program()
    p2.create_function(0x4000, "FUN_00400000")
    p2.create_label(0x5000, "renamed", True, "USER_DEFINED")     # 先占用目标的地址空间
    plan_c = build_plan(p2, re.compile(r"^FUN_00400000$"), "renamed")
    fixed = resolve_conflicts(p2, plan_c)
    check(fixed[0][2] == "renamed_4000",
          "目标名冲突 → 按官方 makeUnique 口径拼地址后缀", fixed[0][2])
    rep = apply_plan(p2, fixed, dry_run=False)
    check(rep["changed"] == 1 and p2.primary_symbol(0x4000).name == "renamed_4000",
          "冲突版计划可正常提交")

    print("== 7. 非法名 → 整批回滚 ==")
    p3 = sample_program()
    snap = p3.dump()
    bad_plan = [(0x401000, "FUN_00401000", "good_name"),
                (0x401100, "FUN_00401100", "bad name")]          # 第二个非法
    rep = apply_plan(p3, bad_plan, dry_run=False)
    check(rep["rolled_back"] and rep["changed"] == 0,
          "任一条失败 → 整批回滚,changed 归零", rep.get("error", "")[:40])
    check(p3.dump() == snap, "符号表回到事务前状态")

    print("== 8. 排除表 ==")
    p4 = sample_program()
    plan4 = build_plan(p4, re.compile(r"^FUN_"), "impl_{addr}",
                       exclude=re.compile(r"00401100"))
    got = [a for a, _, _ in plan4]
    check(0x401100 not in got, "被 exclude 正则命中的地址不进计划", ["%x" % x for x in got])
    check(got == [0x401000, 0x401200, 0x404000],
          "^FUN_ 能匹配 4 个(含 FUN_00404000_marker),排除掉 1 个后剩 3 个",
          ["%x" % x for x in got])
    check([a for a, _, _ in build_plan(p4, re.compile(r"^FUN_"), "impl_{addr}")] ==
          [0x401000, 0x401100, 0x401200, 0x404000],
          "不给 exclude 时 4 个全进计划")

    print("\n结果: %d 项失败" % len(FAIL))
    for x in FAIL:
        print("  FAIL: %s" % x)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
