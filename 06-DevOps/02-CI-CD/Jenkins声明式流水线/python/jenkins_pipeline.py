"""Jenkins 声明式 Pipeline 语义模型(可执行)。

权威依据: jenkins.io «Pipeline Syntax»(doc/book/pipeline/syntax/)。

本 demo 只实现**光读文档看不出来、必须实测/精读才能确定**的那几处规则:

1. **agent 与 timeout 的计数区间不同**(官方明确):
   - 顶层 ``agent``: 先分配 agent, 再应用 ``timeout`` ⇒ **分配 agent 的时间不计入** timeout;
   - stage 级 ``agent``: ``options`` 在**分配 agent 之前**被调用 ⇒ **分配时间计入** timeout,
     因此 agent 供应慢时 Pipeline 可能直接失败。
2. **``post`` 条件的执行顺序是固定的**, 与声明顺序无关:
   ``always -> changed -> fixed -> regression -> aborted -> failure -> success -> unstable
   -> unsuccessful -> cleanup``;``cleanup`` 排在**所有**其它条件之后。
3. **stage 内各段的求值时点**: ``options`` 最先, 然后 ``input`` 暂停, 再进入 ``agent`` 块,
   最后评估 ``when``。三个 ``before*`` 开关按 ``beforeOptions > beforeInput > beforeAgent``
   的优先级把 ``when`` 提前。
4. **stage 结构约束**: 一个 stage 必须有且仅有一个 ``steps`` / ``stages`` / ``parallel`` /
   ``matrix``;``parallel`` 或 ``matrix`` 块**内部**的 stage 不能再嵌套 ``parallel``/``matrix``。
5. **matrix 的两段式求值**: ``axis`` 与 ``exclude`` 在**运行开始前**生成静态 cell 集合;
   而 per-cell 指令(agent/environment/input/options/post/tools/when)在**运行时**求值。
"""

from __future__ import annotations

import itertools

# 官方文档给出的 post 条件执行顺序(逐字顺序, 不可调整)
POST_ORDER = ("always", "changed", "fixed", "regression", "aborted",
              "failure", "success", "unstable", "unsuccessful", "cleanup")

STATUSES = ("success", "failure", "unstable", "aborted")

# stage 级 options 只允许这四类(官方: "stage 级别的 options 只能包含 retry、timeout、
# timestamps 这类步骤, 或与 stage 相关的声明式选项(如 skipDefaultCheckout)")
STAGE_ALLOWED_OPTIONS = ("retry", "timeout", "timestamps", "skipDefaultCheckout")

# 只能在 pipeline 块内出现一次、且不能在 stage 内的指令
PIPELINE_ONLY_ONCE = ("parameters", "triggers")
STAGE_FORBIDDEN_PIPELINE_ONLY = ("disableRestartFromStage",)

AGENT_TYPES = ("any", "none", "label", "node", "docker", "dockerfile", "kubernetes")


class JenkinsError(ValueError):
    """Pipeline 校验 / 运行期错误。"""


# ---------------------------------------------------------------- post

def post_condition_runs(cond: str, status: str, prev_status: str | None) -> bool:
    """单个 ``post`` 条件是否触发。"""
    if cond == "always":
        return True
    if cond == "cleanup":
        return True                      # 无论什么状态都跑, 且排在最后
    if cond == "changed":
        return status != prev_status
    if cond == "fixed":
        return status == "success" and prev_status in ("failure", "unstable")
    if cond == "regression":
        # 本次 failure/unstable/aborted 且上次 success
        return status in ("failure", "unstable", "aborted") and prev_status == "success"
    if cond == "unsuccessful":
        return status != "success"
    return cond == status                # aborted / failure / success / unstable


def run_post(declared: list, status: str, prev_status: str | None) -> list:
    """按**官方固定顺序**返回实际执行的 post 条件(与声明顺序无关)。"""
    if status not in STATUSES:
        raise JenkinsError("未知构建状态: %s" % status)
    return [c for c in POST_ORDER if c in set(declared) and post_condition_runs(c, status, prev_status)]


# ---------------------------------------------------------------- agent / timeout

def times_out(scope: str, alloc_seconds: float, work_seconds: float, limit_seconds: float):
    """返回 (是否超时, 计入 timeout 的秒数)。

    顶层 agent: 分配时间**不计入**;stage 级 agent: 分配时间**计入**。
    """
    if scope not in ("top-level", "stage"):
        raise JenkinsError("scope 只能是 top-level / stage")
    counted = work_seconds if scope == "top-level" else alloc_seconds + work_seconds
    return counted > limit_seconds, counted


def validate_agent(atype: str, at_top_level: bool = True):
    """校验 agent 声明; 返回错误列表(空 = 合法)。"""
    errs = []
    if atype not in AGENT_TYPES:
        errs.append("未知 agent 类型 %s(允许: %s)" % (atype, "/".join(AGENT_TYPES)))
    if atype == "none" and not at_top_level:
        errs.append("agent none 只能出现在 pipeline 顶层")
    return errs


# ---------------------------------------------------------------- stage 求值时点

def stage_eval_order(before_options=False, before_input=False, before_agent=False) -> list:
    """返回 stage 内各段的实际求值顺序。

    基线顺序 ``options -> input -> agent -> when``(官方: options 先调用; input 在
    "应用完 options 之后、进入 agent 块或评估 when 之前"暂停; when 默认在进入 agent
    之后评估)。三个开关把 ``when`` 往前提, 优先级 ``beforeOptions > beforeInput >
    beforeAgent``。
    """
    if before_options:
        return ["when", "options", "input", "agent"]
    if before_input:
        return ["options", "when", "input", "agent"]
    if before_agent:
        return ["options", "input", "when", "agent"]
    return ["options", "input", "agent", "when"]


def when_priority_rank(flags: dict) -> int:
    """返回生效的 before* 开关的优先级权重(数字越大越优先, 0 = 都没开)。"""
    if flags.get("beforeOptions"):
        return 3
    if flags.get("beforeInput"):
        return 2
    if flags.get("beforeAgent"):
        return 1
    return 0


# ---------------------------------------------------------------- stage 结构

def validate_stage_body(body_keys, parent_is_parallel_or_matrix=False) -> list:
    """校验 stage 的"有且仅有一个"约束与 parallel/matrix 的不可嵌套约束。"""
    errs = []
    chosen = [k for k in ("steps", "stages", "parallel", "matrix") if k in body_keys]
    if len(chosen) != 1:
        errs.append("一个 stage 必须有且仅有一个 steps/stages/parallel/matrix, 实际 %d 个: %s"
                    % (len(chosen), chosen or "无"))
    if parent_is_parallel_or_matrix and ("parallel" in body_keys or "matrix" in body_keys):
        errs.append("parallel/matrix 块内的 stage 不能再嵌套 parallel/matrix")
    return errs


def validate_options_scope(scope: str, option_names) -> list:
    errs = []
    for name in option_names:
        if scope == "stage" and name not in STAGE_ALLOWED_OPTIONS:
            errs.append("stage 级 options 不允许 %s(仅限 %s)"
                        % (name, "/".join(STAGE_ALLOWED_OPTIONS)))
        if scope == "stage" and name in STAGE_FORBIDDEN_PIPELINE_ONLY:
            errs.append("%s 不能在 stage 内使用" % name)
    return errs


def validate_once(name: str, occurrences: int, in_stage: bool = False) -> list:
    errs = []
    if name in PIPELINE_ONLY_ONCE:
        if occurrences > 1:
            errs.append("%s 在 pipeline 块内只能出现一次" % name)
        if in_stage:
            errs.append("%s 只能出现在 pipeline 块内" % name)
    return errs


# ---------------------------------------------------------------- parallel / matrix

def run_parallel(stages: list, results: dict, fail_fast=False, global_fail_fast=False):
    """返回 (实际执行的 stage 列表, 被中止的 stage 列表)。

    ``failFast true`` 或 ``options { parallelsAlwaysFailFast() }`` 下, 任一并行 stage
    失败即中止其余未完成的 stage。
    """
    order = list(stages)
    executed, aborted = [], []
    stopped = False
    for s in order:
        if stopped:
            aborted.append(s)
            continue
        executed.append(s)
        if results.get(s) == "failure" and (fail_fast or global_fail_fast):
            stopped = True
    return executed, aborted


def matrix_cells(axes: list, excludes: list | None = None) -> list:
    """按官方 axis/exclude 语义生成静态 cell 集合。

    ``axes``: [(名称, [值...]), ...];``excludes``: [ {轴名: {"values": [...]}} ... ]
    —— 一个 exclude 命中当且仅当它列出的**每个**轴子句都匹配。
    """
    names = [a[0] for a in axes]
    cells = [dict(zip(names, vals)) for vals in itertools.product(*[a[1] for a in axes])]
    for ex in excludes or []:
        keep = []
        for c in cells:
            hit = True
            for axis_name, clause in ex.items():
                vals = clause.get("values")
                notvals = clause.get("notValues")
                if vals is not None and c.get(axis_name) not in vals:
                    hit = False
                if notvals is not None and c.get(axis_name) in notvals:
                    hit = False
            if not hit:
                keep.append(c)
        cells = keep
    return cells


RUNTIME_DIRECTIVES = ("agent", "environment", "input", "options", "post", "tools", "when")
STATIC_DIRECTIVES = ("axes", "excludes")
