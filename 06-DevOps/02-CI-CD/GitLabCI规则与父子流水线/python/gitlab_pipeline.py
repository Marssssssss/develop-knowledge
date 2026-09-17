"""GitLab CI ``rules`` / ``include`` / 父子流水线 语义模型(可执行)。

权威依据:
- docs.gitlab.com «Specify when jobs run with rules»(ci/jobs/job_rules/)
- docs.gitlab.com «CI/CD YAML syntax reference»(ci/yaml/, include 与限制)
- docs.gitlab.com «Downstream pipelines»(ci/pipelines/downstream_pipelines/)

核心事实(全部有原文支撑):
1. **首次匹配即生效** —— "Rules are evaluated in order until the first match. When a match
   is found, the job is either included or excluded from the pipeline."
2. 同一条 rule 里的 ``if`` / ``changes`` / ``exists`` 是 **AND**;一条 rule 只返回一个结果。
3. 默认属性: ``when: on_success``、``allow_failure: false``。但 ``rules`` 里的
   ``when: manual`` 例外 —— 官方明确 "``false`` for jobs that use ``when: manual`` inside
   ``rules``"(与 job 级 ``when: manual`` 的默认 ``true`` **不同**)。
4. ``rules`` 求值发生在任何 job 运行**之前**, 因此**不能**用 job 脚本里产生的 dotenv 变量。
5. ``rules:changes`` 在**没有 Git push 事件**的流水线(标签/计划/手动)里**恒为真**。
6. ``include`` **总是先求值再与 .gitlab-ci.yml 合并**(与 include 关键字的位置无关);
   同名 job/全局关键字以 **.gitlab-ci.yml 为准**;默认最多 **150 个 include**(含嵌套,
   重复计入);解析全部文件的时限 **30 秒**;嵌套 include **以公开用户无上下文执行**。
7. 父子流水线: 层级中最多 **1000 条下游流水线**;父子嵌套深度最多 **两层**;
   单个子流水线最多拼 **3 个配置文件**;子流水线里 ``$CI_PIPELINE_SOURCE`` **恒为**
   ``parent_pipeline``;多项目流水线**无嵌套限制**。
"""

from __future__ import annotations

import re

MAX_INCLUDES = 150            # 官方: 默认每流水线最多 150 个 include(含嵌套, 重复计入)
INCLUDE_BUDGET_S = 30.0       # 官方: 解析全部文件时限 30 秒
INCLUDE_COST_S = 0.2          # 本 demo 口径: 假设每个 include 解析耗时 200ms
MAX_CHANGES_PATTERNS = 50     # 官方: 单条 rule 的 changes 模式上限
MAX_EXISTS_CHECKS = 50000     # 官方: exists 最多检查 50000 个文件
MAX_CHILD_CONFIGS = 3         # 官方: 单个子流水线最多 3 个配置文件
MAX_DOWNSTREAM = 1000         # 官方: 层级中下游流水线默认上限
MAX_CHILD_DEPTH = 2           # 官方: 父子流水线最多两层
PUSH_SOURCES = ("push", "merge_request_event")


class GitLabError(ValueError):
    """流水线创建期错误(与运行时跳过区分开)。"""


def glob_to_regex(pat: str):
    """GitLab 的 ``**`` 通配: ``**/`` 匹配**零个或多个**目录段。

    注意与 GitHub Actions 的差别 —— 这里是标准 glob 语义(``?`` = 单个字符),
    而 GitHub 的 ``?`` 是"前一个字符出现 0 或 1 次"的量词。``fnmatch`` 不能用:
    它把 ``**`` 翻译成 ``.*.*``, 于是 ``cmd/**/*.go`` 匹配不上 ``cmd/main.go``。
    """
    out, i, n = ["^"], 0, len(pat)
    while i < n:
        c = pat[i]
        if c == "*":
            if i + 1 < n and pat[i + 1] == "*":
                if i + 2 < n and pat[i + 2] == "/":
                    out.append("(?:.*/)?")     # `**/` 可匹配零个目录
                    i += 3
                else:
                    out.append(".*")
                    i += 2
            else:
                out.append("[^/]*")
                i += 1
            continue
        if c == "?":
            out.append("[^/]")
            i += 1
            continue
        out.append(re.escape(c))
        i += 1
    out.append("$")
    return re.compile("".join(out))


def glob_match(pat: str, path: str) -> bool:
    return glob_to_regex(pat).match(path) is not None


# ------------------------------------------------------------------ rules:if

_TOKEN = re.compile(r"\s*(\(|\)|&&|\|\||=~|!~|==|!=|\$?[A-Za-z_][A-Za-z0-9_]*|/[^/]*/|'[^']*'|\"[^\"]*\")")


def _tokenize(expr: str) -> list:
    out, i = [], 0
    while i < len(expr):
        m = _TOKEN.match(expr, i)
        if not m:
            raise GitLabError("if 表达式无法解析: %r" % expr[i:])
        out.append(m.group(1))
        i = m.end()
    return out


def eval_if(expr: str, variables: dict) -> bool:
    """求值 GitLab CI 的 ``rules:if`` 表达式。

    与 GitHub 表达式的关键差别: ``=~`` / ``!~`` 右侧是**正则**, 且正则里的变量
    **不会被展开**(官方明确); 未定义的变量按空串处理。
    """
    toks, pos = _tokenize(expr), [0]

    def peek():
        return toks[pos[0]] if pos[0] < len(toks) else None

    def take():
        t = peek()
        pos[0] += 1
        return t

    def atom():
        t = take()
        if t == "(":
            v = or_expr()
            if take() != ")":
                raise GitLabError("括号不匹配")
            return v
        if t is None:
            raise GitLabError("表达式意外结束")
        if t.startswith("'") or t.startswith('"'):
            return t[1:-1]
        if t.startswith("/"):
            return t          # 正则字面量原样返回(绝不能当变量查表)
        if t in ("true", "false"):
            return t == "true"
        if t.startswith("$"):
            return variables.get(t[1:], "")
        return variables.get(t, "")      # 裸名字也按变量处理

    def cmp_expr():
        left = atom()
        if peek() in ("==", "!=", "=~", "!~"):
            op = take()
            right = atom()
            left = _matches(str(left), op, right)
        if peek() in ("==", "!=", "=~", "!~"):
            raise GitLabError("不支持比较链(A == B == C): %r" % (peek(),))
        return left

    def and_expr():
        left = cmp_expr()
        while peek() == "&&":
            take()
            right = cmp_expr()
            left = bool(left) and bool(right)
        return left

    def or_expr():
        left = and_expr()
        while peek() == "||":
            take()
            right = and_expr()
            left = bool(left) or bool(right)
        return left

    result = or_expr()
    if pos[0] != len(toks):
        raise GitLabError("if 表达式有多余 token: %r" % (toks[pos[0]:],))
    return bool(result)


def _matches(left: str, op: str, right) -> bool:
    if op == "==":
        return left == str(right)
    if op == "!=":
        return left != str(right)
    pattern = str(right)
    if pattern.startswith("/") and pattern.endswith("/"):
        rx = pattern[1:-1]        # 注意: 正则内的变量**不展开**(官方明确)
    else:
        rx = re.escape(pattern)
    hit = re.search(rx, left) is not None
    return hit if op == "=~" else not hit


# ------------------------------------------------------------------ rules

def _changes_hit(spec, ctx) -> bool:
    if ctx["pipeline_source"] not in PUSH_SOURCES:
        return True               # 官方: 非推送类流水线 changes 恒真
    if ctx.get("is_new_branch"):
        return True               # 新分支无推送基线, 视为全部变更
    paths = spec.get("paths") if isinstance(spec, dict) else spec
    if paths is None:
        raise GitLabError("changes 需要 paths")
    if len(paths) > MAX_CHANGES_PATTERNS:
        raise GitLabError("changes 模式数超过 %d" % MAX_CHANGES_PATTERNS)
    changed = ctx.get("changed_paths", [])
    if isinstance(spec, dict) and spec.get("compare_to"):
        changed = ctx.get("changed_since", {}).get(spec["compare_to"], changed)
    return any(glob_match(pat, p) for p in changed for pat in paths)


def _exists_hit(spec, ctx) -> bool:
    pats = spec.get("paths") if isinstance(spec, dict) else spec
    if len(pats) > MAX_CHANGES_PATTERNS:
        raise GitLabError("exists 模式数超过 %d" % MAX_CHANGES_PATTERNS)
    files = ctx.get("files", [])
    if len(files) > MAX_EXISTS_CHECKS:
        raise GitLabError("仓库文件数超过 exists 的 %d 检查上限" % MAX_EXISTS_CHECKS)
    return any(glob_match(p, f) for f in files for p in pats)


def select_job(rules: list, ctx: dict):
    """返回 (是否加入流水线, 生效属性)。无 rule 命中 -> **不加入**(不是默认加入)。"""
    for idx, rule in enumerate(rules):
        if "if" in rule and not eval_if(rule["if"], ctx.get("variables", {})):
            continue
        if "changes" in rule and not _changes_hit(rule["changes"], ctx):
            continue
        if "exists" in rule and not _exists_hit(rule["exists"], ctx):
            continue
        when = rule.get("when", "on_success")
        if when == "never":
            return False, {"when": "never", "rule_index": idx}
        allow = rule.get("allow_failure")
        if allow is None:
            # 官方: rules 里的 when: manual 默认 allow_failure=false
            allow = False
        attrs = {"when": when, "allow_failure": allow, "rule_index": idx}
        if "variables" in rule:
            attrs["variables"] = dict(rule["variables"])
        return True, attrs
    return False, {"when": "never", "rule_index": None}


def job_allow_failure_default(where: str) -> bool:
    """job 级 ``when: manual`` 与 ``rules`` 内 ``when: manual`` 的默认值不同。"""
    if where == "job-keyword":
        return True               # 官方: 默认 true for manual jobs
    return False                  # 官方: false for jobs that use when: manual inside rules


# ------------------------------------------------------------------ include

def resolve_includes(files: dict, root: str, overrides: dict | None = None):
    """展开 include 图, 返回 (合并后的配置, 统计)。

    ``files``: {路径: 配置字典}, 每个配置可含 ``include: [路径, ...]``。
    合并顺序 = 深度优先的 include 结果 -> 最后 root 自身覆盖(官方: .gitlab-ci.yml 优先)。
    """
    order, seen, stat = [], [], {"count": 0, "seconds": 0.0, "nested_no_vars": 0}

    def walk(path: str, depth: int):
        cfg = files[path]
        for inc in cfg.get("include", []):
            stat["count"] += 1
            stat["seconds"] += INCLUDE_COST_S
            if stat["count"] > MAX_INCLUDES:
                raise GitLabError("include 数超过上限 %d(含嵌套, 重复计入)" % MAX_INCLUDES)
            if stat["seconds"] > INCLUDE_BUDGET_S:
                raise GitLabError("include 解析超过 %.0f 秒时限" % INCLUDE_BUDGET_S)
            if depth > 0:
                stat["nested_no_vars"] += 1     # 嵌套 include 以公开用户执行, 无变量
            if inc in files:
                walk(inc, depth + 1)
            seen.append(inc)
        order.append(path)

    walk(root, 0)
    merged = {}
    for path in order:                      # order 里 root 在最后 -> 覆盖 include
        for key, val in files[path].items():
            if key == "include":
                continue
            merged[key] = val
    if overrides:
        merged.update(overrides)
    return merged, stat


# ------------------------------------------------------------------ 下游流水线

def check_child_trigger(depth: int, files: list, downstream: int, cross_project=False):
    """校验 trigger 的层级/规模约束, 返回错误列表(空列表 = 合法)。"""
    errs = []
    if not cross_project and depth > MAX_CHILD_DEPTH:
        errs.append("父子流水线最多 %d 层: cannot trigger another level of child pipelines"
                    % MAX_CHILD_DEPTH)
    if len(files) > MAX_CHILD_CONFIGS:
        errs.append("单个子流水线最多拼 %d 个配置文件" % MAX_CHILD_CONFIGS)
    if downstream > MAX_DOWNSTREAM:
        errs.append("层级中下游流水线超过 %d 条" % MAX_DOWNSTREAM)
    return errs


def child_pipeline_source(cross_project: bool) -> str:
    """子流水线里 ``$CI_PIPELINE_SOURCE`` 的取值。"""
    return "pipeline" if cross_project else "parent_pipeline"
