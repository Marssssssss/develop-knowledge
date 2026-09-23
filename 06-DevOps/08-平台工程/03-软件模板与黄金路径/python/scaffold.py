"""Backstage Scaffolder：软件模板（黄金路径）的校验、表达式求值与步骤执行。

事实来源（本轮实读）：
- backstage.io《Writing Templates》：spec.parameters / spec.steps / spec.type / spec.owner 的
  要求；`${{ }}` 与 `{{ }}` 两套语法的分工；`steps.$stepId.output.$property` 取值；
  output.links / output.text 支持 `if`；`backstage.io/time-saved` 是 ISO 8601 时长；
  presentation 可改按钮文案。
- backstage.io《descriptor-format》Kind: Template 小节：apiVersion 有
  `backstage.io/v1beta2` 与（文档示例中出现的）`scaffolder.backstage.io/v1beta3` 两种写法。
- backstage/backstage `plugins/scaffolder-backend/src/scaffolder/tasks/NunjucksWorkflowRunner.ts`：
  步骤按序执行；`each` 会注入 `each.key` / `each.value`；dry-run 时若 action 不支持
  dry run，则按该 action 的 output schema **生成示例输出**（无 schema 则空对象）；
  每步结果写入 `context.steps[step.id].output`。

口径说明（官方未给定量处，本 demo 自行定义并在 README 标注）：
- `generate_example_output` 按 JSON Schema 的 type 生成占位值，官方实现未读。
- 未定义变量按**报错**处理（模板作者写错变量名应当失败，而不是静默变空串）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

TEMPLATE_API_VERSIONS = ("backstage.io/v1beta2", "scaffolder.backstage.io/v1beta3")

EXPR_RE = re.compile(r"\$\{\{(.+?)\}\}")
NJK_RE = re.compile(r"(?<!\$)\{\{(.+?)\}\}")
DURATION_RE = re.compile(
    r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$"
)


class TemplateError(ValueError):
    pass


# ------------------------------------------------------------------ 上下文取值

def lookup(ctx: Dict[str, Any], path: str) -> Any:
    """按点号路径取值，支持 `steps['publish'].output.remoteUrl` 与下标写法。"""
    path = path.strip()
    cur: Any = ctx
    for token in _split_path(path):
        if isinstance(cur, dict):
            if token not in cur:
                raise TemplateError(f"undefined variable {path!r} (missing {token!r})")
            cur = cur[token]
        else:
            raise TemplateError(f"cannot descend into {type(cur).__name__} at {path!r}")
    return cur


def _split_path(path: str) -> List[str]:
    tokens: List[str] = []
    buf = ""
    i = 0
    while i < len(path):
        ch = path[i]
        if ch == ".":
            if buf:
                tokens.append(buf)
                buf = ""
            i += 1
        elif ch == "[":
            if buf:
                tokens.append(buf)
                buf = ""
            end = path.index("]", i)
            tokens.append(path[i + 1:end].strip("'\""))
            i = end + 1
        else:
            buf += ch
            i += 1
    if buf:
        tokens.append(buf)
    return tokens


# -------------------------------------------------------------------- 渲染

def render_typed(text: str, ctx: Dict[str, Any]) -> Any:
    """`${{ }}`：整串就是一个表达式时**保留类型**，混排时退化成字符串拼接。"""
    if not isinstance(text, str):
        return text
    stripped = text.strip()
    m = EXPR_RE.fullmatch(stripped)
    if m:
        return lookup(ctx, m.group(1))
    return EXPR_RE.sub(lambda mo: _stringify(lookup(ctx, mo.group(1))), text)


def render_nunjucks(text: str, ctx: Dict[str, Any]) -> str:
    """`{{ }}`：模板文件正文里的 Nunjucks 插值，结果始终是字符串。"""
    return NJK_RE.sub(lambda mo: _stringify(lookup(ctx, mo.group(1))), text)


def _stringify(value: Any) -> str:
    if value is True:
        return "True"
    if value is False:
        return "False"
    if value is None:
        return ""
    return str(value)


def render_input(node: Any, ctx: Dict[str, Any]) -> Any:
    """递归渲染一个 step 的 input（可能是嵌套的 dict / list / 标量）。"""
    if isinstance(node, dict):
        return {k: render_input(v, ctx) for k, v in node.items()}
    if isinstance(node, list):
        return [render_input(v, ctx) for v in node]
    return render_typed(node, ctx)


# -------------------------------------------------------------------- 动作

@dataclass
class Action:
    """一个 scaffolder 动作：输入校验 + 处理函数 + 可选的 output schema。"""
    id: str
    handler: Callable[[Dict[str, Any]], Dict[str, Any]]
    input_schema: Optional[Dict[str, Any]] = None
    output_schema: Optional[Dict[str, Any]] = None
    supports_dry_run: bool = True


def generate_example_output(schema: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """按 JSON Schema 生成示例输出（官方实现未读，本 demo 以 type 映射占位值）。"""
    if not schema or "properties" not in schema:
        return {}
    out: Dict[str, Any] = {}
    for name, prop in schema["properties"].items():
        ptype = prop.get("type", "string")
        out[name] = {
            "string": "<string>", "number": 0, "integer": 0,
            "boolean": False, "object": {}, "array": [],
        }.get(ptype, "<value>")
    return out


def validate_input(action: Action, data: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    schema = action.input_schema
    if not schema:
        return errors
    for name in schema.get("required", []):
        if name not in data or data[name] in (None, ""):
            errors.append(f"action {action.id} requires input {name!r}")
    return errors


# -------------------------------------------------------------------- 模板

@dataclass
class Template:
    api_version: str
    name: str
    spec: Dict[str, Any]
    annotations: Dict[str, str] = field(default_factory=dict)


def validate_template(tpl: Template) -> List[str]:
    errors: List[str] = []
    if tpl.api_version not in TEMPLATE_API_VERSIONS:
        errors.append(f"apiVersion must be one of {TEMPLATE_API_VERSIONS}")
    if not tpl.name:
        errors.append("metadata.name is required")
    spec = tpl.spec
    for key in ("type", "parameters", "steps"):
        if key not in spec:
            errors.append(f"spec.{key} is required")
    # 口径分歧：descriptor-format 把 spec.owner 标为 [optional]，但正文写 "This field is
    # required"；本 demo 不强制，只记录（见 README）。
    steps = spec.get("steps") or []
    seen = set()
    for step in steps:
        if not isinstance(step, dict):
            errors.append("each step must be an object")
            continue
        for key in ("id", "action"):
            if key not in step:
                errors.append(f"step requires {key}")
        sid = step.get("id")
        if sid in seen:
            errors.append(f"duplicate step id {sid!r}")
        seen.add(sid)
    return errors


def parse_duration(text: str) -> float:
    """解析 ISO 8601 时长（如 PT4H / PT15M / P1DT2H），返回秒。"""
    m = DURATION_RE.match(text or "")
    if not m or text in ("P", "PT"):
        raise TemplateError(f"invalid ISO 8601 duration {text!r}")
    days, hours, minutes, seconds = m.groups()
    return (int(days or 0) * 86400 + int(hours or 0) * 3600
            + int(minutes or 0) * 60 + float(seconds or 0))


# -------------------------------------------------------------------- 执行

@dataclass
class StepResult:
    id: str
    status: str           # "completed" | "skipped" | "dry-run"
    output: Dict[str, Any]


def execute(tpl: Template, parameters: Dict[str, Any], actions: Dict[str, Action],
            dry_run: bool = False) -> Tuple[Dict[str, Any], List[StepResult]]:
    """按序执行步骤，返回 (steps 上下文, 每步结果)。"""
    errors = validate_template(tpl)
    if errors:
        raise TemplateError(f"invalid template: {errors}")

    parameters = _apply_defaults(tpl.spec["parameters"], parameters)
    missing = _missing_required(tpl.spec["parameters"], parameters)
    if missing:
        raise TemplateError(f"missing required parameters: {missing}")

    ctx: Dict[str, Any] = {"parameters": parameters, "steps": {}, "each": None}
    results: List[StepResult] = []

    for step in tpl.spec["steps"]:
        action = actions.get(step["action"])
        if action is None:
            raise TemplateError(f"unknown action {step['action']!r}")

        if "if" in step:
            ctx["steps"] = ctx["steps"]
            if not _truthy(render_typed(step["if"], ctx)):
                ctx["steps"][step["id"]] = {"output": {}}
                results.append(StepResult(step["id"], "skipped", {}))
                continue

        each = render_typed(step["each"], ctx) if "each" in step else None

        if each is not None:
            if not isinstance(each, dict):
                raise TemplateError("step `each` must render to an object")
            outputs: Dict[str, Any] = {}
            for key, value in each.items():
                local = dict(ctx)
                local["each"] = {"key": key, "value": value}
                rendered = render_input(step.get("input", {}), local)
                outputs[key] = _run_or_dryrun(action, rendered, dry_run)
            ctx["steps"][step["id"]] = {"output": {"results": outputs}}
            results.append(StepResult(step["id"], "completed",
                                      {"results": outputs}))
            continue

        inputs = render_input(step.get("input", {}), ctx)
        errs = validate_input(action, inputs)
        if errs:
            raise TemplateError(f"{errs}")
        output = _run_or_dryrun(action, inputs, dry_run)
        ctx["steps"][step["id"]] = {"output": output}
        status = "dry-run" if dry_run and not action.supports_dry_run else "completed"
        results.append(StepResult(step["id"], status, output))

    return ctx, results


def _run_or_dryrun(action: Action, inputs: Dict[str, Any], dry_run: bool) -> Dict[str, Any]:
    if dry_run and not action.supports_dry_run:
        return generate_example_output(action.output_schema)
    return action.handler(inputs) or {}


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1")
    return bool(value)


def _apply_defaults(parameters_spec: List[Dict[str, Any]],
                    supplied: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(supplied)
    for page in parameters_spec or []:
        for name, prop in (page.get("properties") or {}).items():
            if name not in merged and "default" in prop:
                merged[name] = prop["default"]
    return merged


def _missing_required(parameters_spec: List[Dict[str, Any]],
                      supplied: Dict[str, Any]) -> List[str]:
    missing: List[str] = []
    for page in parameters_spec or []:
        for name in page.get("required", []):
            if name not in supplied or supplied[name] in (None, ""):
                missing.append(name)
    return missing


def render_outputs(tpl: Template, ctx: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """渲染 spec.output 的 links / text，带 `if` 的条目求值后过滤。"""
    spec = tpl.spec.get("output") or {}
    out: Dict[str, List[Dict[str, Any]]] = {"links": [], "text": []}
    for section in ("links", "text"):
        for item in spec.get(section, []) or []:
            if "if" in item and not _truthy(render_typed(item["if"], ctx)):
                continue
            rendered = {k: render_typed(v, ctx) for k, v in item.items() if k != "if"}
            out[section].append(rendered)
    return out
