#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""analyzeHeadless 的语义模型与自检 —— 选项组合、脚本处置、参数消费。

依据(Ghidra 官方 Headless Analyzer README, 本轮实读):
  * HeadlessContinuationOption 四种取值与 4×4 合并表(官方给了完整表格);
    并注明:**当 Script1 设为 ABORT 或 ABORT_AND_DELETE 时 Script2 不会运行**
    (除非 Script2 是被 Script1 调用的子脚本);
  * 每个程序开始时续延选项默认为 CONTINUE;**选项在当前脚本跑完后才生效**
    (设 ABORT 不会立刻中止当前脚本,而是中止紧随其后的分析/脚本);
  * 单个脚本内多次设置以**最后一次**为准;子脚本设的选项在主脚本跑完后生效;
  * `-process` 下删除程序必须给 `-okToDelete`,且 `-readOnly` 时无论如何不能删;
  * `analysisTimeoutOccurred()` 仅在「设了 -analysisTimeoutPerFile + 分析已启用且完成
    + 当前脚本是 postScript」时可用。

运行: python headless_semantics.py     退出码 0 表示全部断言通过。
"""

import sys

from headless_cli import (CliError, build_command, consume_script_args,
                          default_recursive_depth, parse_properties, plan,
                          wildcard_needs_quote)

CONTINUATIONS = ("ABORT", "ABORT_AND_DELETE", "CONTINUE_THEN_DELETE", "CONTINUE")

# 官方表格逐格抄录:行 = Script1 先设,列 = Script2 后设
OFFICIAL_TABLE = {
    "ABORT": {"ABORT": "ABORT", "ABORT_AND_DELETE": "ABORT",
              "CONTINUE_THEN_DELETE": "ABORT", "CONTINUE": "ABORT"},
    "ABORT_AND_DELETE": {"ABORT": "ABORT_AND_DELETE",
                         "ABORT_AND_DELETE": "ABORT_AND_DELETE",
                         "CONTINUE_THEN_DELETE": "ABORT_AND_DELETE",
                         "CONTINUE": "ABORT_AND_DELETE"},
    "CONTINUE_THEN_DELETE": {"ABORT": "ABORT_AND_DELETE",
                             "ABORT_AND_DELETE": "ABORT_AND_DELETE",
                             "CONTINUE_THEN_DELETE": "CONTINUE_THEN_DELETE",
                             "CONTINUE": "CONTINUE_THEN_DELETE"},
    "CONTINUE": {"ABORT": "ABORT", "ABORT_AND_DELETE": "ABORT_AND_DELETE",
                 "CONTINUE_THEN_DELETE": "CONTINUE_THEN_DELETE",
                 "CONTINUE": "CONTINUE"},
}


def merge(a, b):
    """按官方表格反推出的合并规则(用于自校验,而不是直接抄表)。

    规则:
      1) Script1 一旦是 ABORT/ABORT_AND_DELETE,Script2 根本不运行 → 结果即 Script1;
      2) 否则 Script2 的「删除」意图被保留:ABORT+CTD 组合升格为 ABORT_AND_DELETE;
      3) CONTINUE 视为无意见,不削弱 Script1 的意图。
    """
    if a in ("ABORT", "ABORT_AND_DELETE"):
        return a
    if b == "CONTINUE":
        return a
    if b in ("ABORT_AND_DELETE", "CONTINUE_THEN_DELETE"):
        return "ABORT_AND_DELETE" if (b == "ABORT_AND_DELETE") else "CONTINUE_THEN_DELETE"
    return "ABORT_AND_DELETE" if a == "CONTINUE_THEN_DELETE" else "ABORT"


def script2_runs(first_option):
    """官方脚注:Script1 为 ABORT / ABORT_AND_DELETE 时 Script2 不运行。"""
    return first_option not in ("ABORT", "ABORT_AND_DELETE")


def outcome(mode, option, ok_to_delete=False, read_only=False):
    """给定模式与续延选项,返回 (是否继续后续处理, 程序最终是否保留)。"""
    cont = {"ABORT": False, "ABORT_AND_DELETE": False,
            "CONTINUE_THEN_DELETE": True, "CONTINUE": True}[option]
    if mode == "import":
        keep = option == "ABORT" or option in ("CONTINUE",)
        if read_only and option in ("ABORT", "CONTINUE"):
            keep = False                     # -readOnly 导入的文件不保存
        return cont, keep
    keep = option in ("ABORT", "CONTINUE")   # -process:ABORT 会保存改动
    if not keep:
        if read_only:
            keep = True                      # 官方:-process + -readOnly 时不能删
        elif not ok_to_delete:
            keep = True                      # 未给 -okToDelete → 只警告,不删
    return cont, keep


def timeout_status_available(phase, timeout_set, analysis_enabled):
    """analysisTimeoutOccurred() 的可用条件(官方三条同时满足才可用)。"""
    return phase == "postScript" and timeout_set and analysis_enabled


FAIL = []


def check(cond, label, detail=""):
    if not cond:
        FAIL.append(label)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", label,
                           ("  <- " + str(detail)) if detail else ""))
    return cond


def main():
    print("== 1. 用规则复现官方 4×4 续延合并表 ==")
    bad = []
    for a in CONTINUATIONS:
        for b in CONTINUATIONS:
            if merge(a, b) != OFFICIAL_TABLE[a][b]:
                bad.append((a, b, merge(a, b), OFFICIAL_TABLE[a][b]))
    check(bad == [], "16 格逐格一致(规则 != 抄表,能反查规则)", bad)
    check(merge("CONTINUE_THEN_DELETE", "ABORT") == "ABORT_AND_DELETE",
          "CTD 后接 ABORT → ABORT_AND_DELETE(删除意图被合并)")
    check(merge("ABORT", "CONTINUE_THEN_DELETE") == "ABORT",
          "ABORT 后接 CTD → 仍 ABORT:合并表不满足交换律")
    check(merge("CONTINUE", "CONTINUE") == "CONTINUE", "默认 CONTINUE")
    check(not script2_runs("ABORT_AND_DELETE") and script2_runs("CONTINUE_THEN_DELETE"),
          "Script2 是否运行取决于 Script1 的选项")

    print("== 2. 处置与安全门 ==")
    check(outcome("import", "ABORT") == (False, True), "import+ABORT:不继续但已导入")
    check(outcome("import", "ABORT_AND_DELETE") == (False, False), "import+AD:不导入")
    check(outcome("import", "CONTINUE_THEN_DELETE") == (True, False), "import+CTD:跑完但不导入")
    check(outcome("import", "CONTINUE", read_only=True) == (True, False),
          "-readOnly 导入 → 改动不保存(隐含丢弃)")
    check(outcome("process", "ABORT") == (False, True), "process+ABORT:改动仍被保存")
    check(outcome("process", "ABORT_AND_DELETE", ok_to_delete=False) == (False, True),
          "process 下想删但没给 -okToDelete → 只警告不删")
    check(outcome("process", "ABORT_AND_DELETE", ok_to_delete=True) == (False, False),
          "给了 -okToDelete 才真的删")
    check(outcome("process", "CONTINUE_THEN_DELETE", ok_to_delete=True, read_only=True)
          == (True, True), "-process + -readOnly:即使脚本要求删也不删")
    check(timeout_status_available("postScript", True, True)
          and not timeout_status_available("preScript", True, True),
          "analysisTimeoutOccurred() 只在 postScript 可用")

    print("== 3. 命令行构造与非法组合拦截 ==")
    cmd = build_command("/Users/user/ghidra/projects", "MyProject",
                        imports=["hello.exe"],
                        pre=[("Script.java", ["arg1", "arg2"])], noanalysis=True)
    check(cmd == "analyzeHeadless /Users/user/ghidra/projects MyProject "
                 "-import hello.exe -preScript Script.java arg1 arg2 -noanalysis",
          "复现官方示例的命令行", cmd)
    for label, kwargs in [
        ("-import 与 -process 互斥", dict(imports=["a.exe"], process="p")),
        ("脚本名不能带路径", dict(imports=["a.exe"], pre=["/tmp/S.java"])),
        ("脚本名必须带扩展名", dict(imports=["a.exe"], pre=["NoExt"])),
        ("-readOnly 与 -overwrite 互斥", dict(imports=["a.exe"], read_only=True,
                                              overwrite=True)),
    ]:
        try:
            build_command("/proj", "P", **kwargs)
            check(False, label, "未拦截")
        except CliError as e:
            check(True, label, str(e))
    try:
        build_command(imports=["a.exe"])
        check(False, "缺 project_location/name 被拦截", "未拦截")
    except CliError as e:
        check(True, "缺 project_location/name 被拦截", str(e))
    check(build_command("/p", "P", max_cpu=-2).endswith("-max-cpu 1"),
          "max-cpu 传 0/负数等价于 1",
          build_command("/p", "P", max_cpu=-2).split("-max-cpu")[-1].strip())
    check(build_command("/p", "P", server_url="ghidra://srv:13100/repo/f1")
          == "analyzeHeadless ghidra://srv:13100/repo/f1",
          "仓库 URL 形式可用")

    print("== 4. 执行计划(顺序即语义) ==")
    p = plan("/p", "P", imports=["a.exe"], pre=["Pre.java", "Post.java"],
             post=["Pst.java"], noanalysis=True)
    check([s[0] for s in p] == ["import", "preScript", "preScript", "postScript",
                                "save-import"],
          "preScript 在分析之前、postScript 在之后;-noanalysis 时无分析步",
          [s[0] for s in p])
    check(p[1][1] == "Pre.java" and p[2][1] == "Post.java", "pre/post 脚本按命令行顺序")
    check(plan("/p", "P", imports=["d"], delete_project=True)[-1] == ("delete-project", "P"),
          "-import + -deleteProject → 项目最终被删")
    check(plan("/p", "P", imports=["d"], read_only=True)[-1][0] == "discard-changes",
          "-readOnly → 末尾是丢弃改动")
    check([s[0] for s in plan("/p", "P", process="x", post=["S.java"])]
          == ["process", "analysis", "postScript", "save-process"],
          "process 模式默认跑分析并保存改动")

    print("== 5. 脚本参数与 .properties 的消费顺序 ==")
    check(consume_script_args(["a", "b"], 2) == ["a", "b"], "参数按序消费")
    try:
        consume_script_args(["a"], 3)
        check(False, "参数耗尽应抛 IndexOutOfBounds", "未抛")
    except IndexError as e:
        check(True, "参数耗尽抛 IndexOutOfBounds(覆盖 .properties 的后果)", str(e))
    props, order = parse_properties(
        "# 注释行\n! 也是注释\nChoose a file Please choose a file: = /tmp/help.exe\n"
        "Asking for bytes Put some bytes here -- = AA BB CC\n")
    check(order == ["Choose a file Please choose a file:", "Asking for bytes Put some bytes here --"],
          "properties 的 key 是「空格拼接的参数串」", list(props))
    check(props["Asking for bytes Put some bytes here --"] == "AA BB CC",
          "行内 # 与 ! 外的内容原样保留")
    check(default_recursive_depth(["dir"]) == {"dir": 0, "file": 1},
          "-recursive 默认深度:目录 0/文件 1(0 = 不进容器文件)")
    check(wildcard_needs_quote("a*") and not wildcard_needs_quote("algo1"),
          "-process 通配符需单引号防 shell 抢跑")

    print("\n结果: %d 项失败" % len(FAIL))
    for x in FAIL:
        print("  FAIL: %s" % x)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
