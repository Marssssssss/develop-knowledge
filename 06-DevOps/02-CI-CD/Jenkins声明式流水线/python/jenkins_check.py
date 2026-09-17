"""自检: Jenkins 声明式 Pipeline 的语义断言。

结论与顺序均来自 jenkins.io «Pipeline Syntax» 原文(见 README「参考资料」)。
运行: python jenkins_check.py
"""

from __future__ import annotations

from jenkins_pipeline import (
    AGENT_TYPES,
    POST_ORDER,
    STAGE_ALLOWED_OPTIONS,
    JenkinsError,
    matrix_cells,
    post_condition_runs,
    run_parallel,
    run_post,
    stage_eval_order,
    times_out,
    validate_agent,
    validate_once,
    validate_options_scope,
    validate_stage_body,
    when_priority_rank,
)

PASS, FAIL = 0, 0
FAILED = []


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILED.append("%s %s" % (label, detail))
        print("  FAIL %s %s" % (label, detail))


def raises(fn, *a, **kw):
    try:
        fn(*a, **kw)
        return False
    except (ValueError, JenkinsError):
        return True


# ============================ 1. post 条件 ============================

def test_post():
    print("[1] post 条件语义与固定执行顺序")
    check("官方顺序逐字一致",
          list(POST_ORDER) == ["always", "changed", "fixed", "regression", "aborted",
                               "failure", "success", "unstable", "unsuccessful", "cleanup"])

    # 单条件语义
    check("always 恒触发", post_condition_runs("always", "failure", "failure"))
    check("cleanup 恒触发", post_condition_runs("cleanup", "aborted", "success"))
    check("success 只在 success 触发",
          post_condition_runs("success", "success", "failure")
          and not post_condition_runs("success", "unstable", "failure"))
    check("failure 只在 failure 触发",
          post_condition_runs("failure", "failure", "success")
          and not post_condition_runs("failure", "unstable", "success"))
    check("unstable 只在 unstable 触发",
          post_condition_runs("unstable", "unstable", "success")
          and not post_condition_runs("unstable", "failure", "success"))
    check("aborted 只在 aborted 触发",
          post_condition_runs("aborted", "aborted", "success")
          and not post_condition_runs("aborted", "failure", "success"))
    check("unsuccessful = 非 success",
          post_condition_runs("unsuccessful", "unstable", "success")
          and post_condition_runs("unsuccessful", "aborted", "success")
          and not post_condition_runs("unsuccessful", "success", "failure"))

    check("changed = 与上次状态不同",
          post_condition_runs("changed", "success", "failure")
          and not post_condition_runs("changed", "failure", "failure"))
    check("fixed = 本次 success 且上次 failure/unstable",
          post_condition_runs("fixed", "success", "failure")
          and post_condition_runs("fixed", "success", "unstable")
          and not post_condition_runs("fixed", "success", "success"))
    check("regression = 本次 failure/unstable/aborted 且上次 success",
          post_condition_runs("regression", "failure", "success")
          and post_condition_runs("regression", "unstable", "success")
          and post_condition_runs("regression", "aborted", "success")
          and not post_condition_runs("regression", "failure", "failure"))

    # 声明顺序不影响执行顺序
    declared = ["cleanup", "success", "always", "failure", "changed"]
    got = run_post(declared, "failure", "success")
    check("执行顺序按官方顺序而非声明顺序", got == ["always", "changed", "failure", "cleanup"], str(got))
    got = run_post(declared, "success", "success")
    check("success 场景下 failure 不触发", "failure" not in got, str(got))
    check("cleanup 永远排最后", got[-1] == "cleanup", str(got))
    got = run_post(["unsuccessful", "always", "success", "unstable"], "unstable", "success")
    check("unstable 场景: unstable 与 unsuccessful 触发而 success 不触发",
          got == ["always", "unstable", "unsuccessful"], str(got))
    got = run_post(["cleanup", "regression", "fixed", "changed"], "success", "failure")
    check("failure -> success: fixed 与 changed 都触发且 fixed 在前",
          got == ["changed", "fixed", "cleanup"], str(got))
    got = run_post(["regression", "fixed"], "success", "failure")
    check("fixed 与 regression 互斥(前者要求本次 success)", got == ["fixed"], str(got))
    check("未知状态报错", raises(run_post, ["always"], "shiny", None))
    check("空声明返回空列表", run_post([], "success", "success") == [])


# ============================ 2. agent 与 timeout ============================

def test_agent_timeout():
    print("[2] agent 与 timeout 的计数区间")
    ok, counted = times_out("top-level", alloc_seconds=600, work_seconds=100, limit_seconds=300)
    check("顶层 agent: 分配 600s 不计入(100 < 300 不超时)", not ok and counted == 100, str(counted))
    ok, counted = times_out("stage", alloc_seconds=600, work_seconds=100, limit_seconds=300)
    check("stage 级 agent: 分配 600s 计入(700 > 300 超时)", ok and counted == 700, str(counted))
    ok, _ = times_out("stage", 0, 301, 300)
    check("stage 级 agent: 零分配时间时边界与顶层一致", ok)
    ok, _ = times_out("top-level", 0, 300, 300)
    check("恰好等于上限不算超时", not ok)
    ok, _ = times_out("stage", 0, 300, 300)
    check("stage 级恰好等于上限也不算超时", not ok)
    check("非法 scope 报错", raises(times_out, "pipeline", 1, 1, 1))

    check("agent 类型全集合法", all(validate_agent(t) == [] for t in AGENT_TYPES))
    check("agent none 只能顶层", validate_agent("none", at_top_level=False) != [])
    check("agent docker 可在 stage 用", validate_agent("docker", at_top_level=False) == [])
    check("未知 agent 类型报错", validate_agent("vm") != [])


# ============================ 3. stage 求值时点 ============================

def test_stage_order():
    print("[3] stage 内求值时点与 before* 开关")
    check("默认顺序 options -> input -> agent -> when",
          stage_eval_order() == ["options", "input", "agent", "when"])
    check("beforeAgent: when 提到 agent 之前",
          stage_eval_order(before_agent=True) == ["options", "input", "when", "agent"])
    check("beforeInput: when 提到 input 之前(且早于 beforeAgent)",
          stage_eval_order(before_input=True) == ["options", "when", "input", "agent"])
    check("beforeOptions: when 提到最前",
          stage_eval_order(before_options=True) == ["when", "options", "input", "agent"])
    check("beforeOptions 同时置位时仍最前",
          stage_eval_order(True, True, True) == ["when", "options", "input", "agent"])
    check("beforeInput 优先于 beforeAgent",
          stage_eval_order(before_input=True, before_agent=True)
          == ["options", "when", "input", "agent"])
    check("优先级 beforeOptions > beforeInput > beforeAgent",
          when_priority_rank({"beforeOptions": True, "beforeInput": True, "beforeAgent": True}) == 3
          and when_priority_rank({"beforeInput": True, "beforeAgent": True}) == 2
          and when_priority_rank({"beforeAgent": True}) == 1
          and when_priority_rank({}) == 0)
    check("when 永远不排在最末以外的位置之前 options",
          stage_eval_order(before_agent=True).index("when") == 2)


# ============================ 4. 结构约束 ============================

def test_structure():
    print("[4] stage 结构与指令作用域")
    check("只有 steps 合法", validate_stage_body(["steps"]) == [])
    check("steps + stages 非法", validate_stage_body(["steps", "stages"]) != [])
    check("四者都无非法", validate_stage_body([]) != [])
    check("parallel 块内的 stage 不能再 parallel",
          validate_stage_body(["parallel"], parent_is_parallel_or_matrix=True) != [])
    check("parallel 块内的 stage 可以只有 steps",
          validate_stage_body(["steps"], parent_is_parallel_or_matrix=True) == [])
    check("matrix 块内不能再 matrix",
          validate_stage_body(["matrix"], parent_is_parallel_or_matrix=True) != [])
    check("顶层 stage 用 matrix 合法",
          validate_stage_body(["matrix"]) == [])

    check("stage 级 options 只允许四类",
          set(STAGE_ALLOWED_OPTIONS) == {"retry", "timeout", "timestamps", "skipDefaultCheckout"})
    check("stage 级允许 timeout", validate_options_scope("stage", ["timeout"]) == [])
    check("stage 级不允许 buildDiscarder",
          validate_options_scope("stage", ["buildDiscarder"]) != [])
    check("pipeline 级允许 buildDiscarder",
          validate_options_scope("pipeline", ["buildDiscarder"]) == [])
    check("pipeline 级允许 parallelsAlwaysFailFast",
          validate_options_scope("pipeline", ["parallelsAlwaysFailFast"]) == [])
    check("stage 级不允许 parallelsAlwaysFailFast",
          validate_options_scope("stage", ["parallelsAlwaysFailFast"]) != [])
    check("disableRestartFromStage 不能在 stage 内",
          validate_options_scope("stage", ["disableRestartFromStage"]) != [])

    check("parameters 只能一次", validate_once("parameters", 1) == [])
    check("parameters 两次报错", validate_once("parameters", 2) != [])
    check("triggers 只能一次", validate_once("triggers", 2) != [])
    check("triggers 不能进 stage", validate_once("triggers", 1, in_stage=True) != [])
    check("stages 不在 pipeline-only 列表里", validate_once("stages", 3) == [])


# ============================ 5. parallel / matrix ============================

def test_parallel_matrix():
    print("[5] parallel 的 failFast 与 matrix 的两段式求值")
    stages = ["A", "B", "C"]
    ex, ab = run_parallel(stages, {"A": "success", "B": "failure", "C": "success"}, fail_fast=True)
    check("failFast: B 失败后 C 被中止", ex == ["A", "B"] and ab == ["C"], str((ex, ab)))
    ex, ab = run_parallel(stages, {"A": "success", "B": "failure", "C": "success"}, fail_fast=False)
    check("未开 failFast: 三个 stage 全部执行", ex == stages and ab == [], str((ex, ab)))
    ex, ab = run_parallel(stages, {"A": "success", "B": "failure", "C": "success"},
                          global_fail_fast=True)
    check("parallelsAlwaysFailFast(): 等效于 failFast true", ab == ["C"], str(ab))
    ex, ab = run_parallel(stages, {"A": "failure", "B": "success", "C": "success"}, fail_fast=True)
    check("首个失败即中止(A 失败 -> B/C 中止)", ex == ["A"] and ab == ["B", "C"], str((ex, ab)))
    ex, ab = run_parallel(stages, {"A": "unstable", "B": "unstable", "C": "unstable"},
                          fail_fast=True)
    check("全 unstable 不触发 failFast(unstable 不等于 failure)", ex == stages and ab == [], str((ex, ab)))

    axes = [("PLATFORM", ["linux", "mac", "windows"]),
            ("BROWSER", ["chrome", "edge", "firefox", "safari"])]
    check("无 exclude 时 cell 数 = 3 × 4 = 12", len(matrix_cells(axes)) == 12)
    ex1 = [{"PLATFORM": {"values": ["mac"]}, "BROWSER": {"values": ["safari"]}}]
    check("单条 exclude(两轴同时匹配)去掉 1 个 cell", len(matrix_cells(axes, ex1)) == 11)
    ex2 = [{"PLATFORM": {"values": ["mac"]}, "BROWSER": {"values": ["edge"]}},
           {"PLATFORM": {"notValues": ["windows"]}, "BROWSER": {"values": ["edge"]}}]
    cells = matrix_cells(axes, ex2)
    check("notValues 排除 (linux|mac) × edge", len(cells) == 10, str(len(cells)))
    check("被排除的 cell 确实不在结果里",
          not any(c["BROWSER"] == "edge" and c["PLATFORM"] in ("mac", "linux") for c in cells))
    check("windows × edge 保留(notValues 只排非 windows)",
          any(c["PLATFORM"] == "windows" and c["BROWSER"] == "edge" for c in cells))
    check("axes 是静态集合: 同一组输入两次调用结果一致",
          matrix_cells(axes, ex2) == cells)
    check("单轴 matrix", len(matrix_cells([("JDK", ["8", "11", "17"])])) == 3)
    check("exclude 未匹配任何 axis 值时无副作用",
          len(matrix_cells(axes, [{"PLATFORM": {"values": ["bsd"]}}])) == 12)


def main():
    print("Jenkins 声明式 Pipeline 语义自检")
    test_post()
    test_agent_timeout()
    test_stage_order()
    test_structure()
    test_parallel_matrix()
    print("\n断言 %d 通过 / %d 失败" % (PASS, FAIL))
    if FAILED:
        print("失败明细:")
        for f in FAILED:
            print("  - " + f)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
