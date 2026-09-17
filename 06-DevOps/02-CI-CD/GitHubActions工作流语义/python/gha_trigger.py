"""GitHub Actions ``on:`` 触发过滤与 ``strategy.matrix`` 展开语义模型(可执行)。

权威依据: docs.github.com «Workflow syntax for GitHub Actions»
- 过滤器模式: ``*`` 匹配任意字符但**不含** ``/``;``**`` 匹配任意字符;**``?``** 是"前一个字符
  出现 0 或 1 次";**``+``** 是"前一个字符出现 1 次或多次";``\\`` 转义。
- 顺序语义: 负向模式(``!`` 前缀)写在正向匹配**之后**会排除该 ref;正向模式写在负向**之后**
  会**重新包含**。因此必须"最后命中的模式胜出", 不是简单的 any/all。
- ``branches`` 与 ``branches-ignore`` **不能**在同一事件上同时出现(``tags`` 同理);
  ``paths`` 与 ``paths-ignore`` 同理 —— 这类组合是**工作流校验错误**。
- 只声明 ``tags``/``tags-ignore`` 时, 分支推送**不会**触发;只声明 ``branches``/``branches-ignore``
  时, 标签推送**不会**触发;两者都不写则分支与标签都会触发。
- 路径过滤**不适用于标签推送**;``paths-ignore`` 只有在**全部**变更路径都命中时才不运行。
- 限制行为: 单次 push 提交数 **> 1000** 时工作流**总是**运行(绕过路径过滤);生成的 diff
  超过 **3000** 个文件时, 若匹配项不在前 3000 个中则**不会**运行。
- ``matrix.include``: 逐条处理, 若某条 ``include`` 不会覆盖**原始矩阵值**则把它并入全部原始组合;
  否则**新建**一个组合。原始矩阵值不可被覆盖, 但 include 新增的键可被后续 include 覆盖。
"""

from __future__ import annotations

import itertools
import re

REF_EVENTS = ("push", "pull_request", "pull_request_target")


def pattern_to_regex(pat: str) -> re.Pattern:
    out, i, n = ["^"], 0, len(pat)
    while i < n:
        c = pat[i]
        if c == "\\" and i + 1 < n:
            out.append(re.escape(pat[i + 1]))
            i += 2
            continue
        if c == "*":
            if i + 1 < n and pat[i + 1] == "*":
                out.append(".*")
                i += 2
            else:
                out.append("[^/]*")
                i += 1
            continue
        if c in "?+":
            # 量词: 修饰前一个已发出的原子; 出现在首位时退化为字面量
            out.append(c if len(out) > 1 else re.escape(c))
            i += 1
            continue
        out.append(re.escape(c))
        i += 1
    out.append("$")
    return re.compile("".join(out))


def match_ordered(patterns, value: str) -> bool:
    """正向列表的顺序语义: 最后命中的模式胜出; 从未命中则为 False。"""
    decision = False
    for p in patterns:
        neg = p.startswith("!")
        body = p[1:] if neg else p
        if pattern_to_regex(body).match(value):
            decision = not neg
    return decision


def match_any(patterns, value: str) -> bool:
    """``*-ignore`` 列表: 纯排除集合, 命中任意一条即排除。"""
    return any(pattern_to_regex(p).match(value) for p in patterns)


def _check_exclusive(filt: dict, a: str, b: str):
    """校验工作流层面的互斥组合(命中即 YAML 校验失败, 不是运行时跳过)。"""
    if a in filt and b in filt:
        raise ValueError("on.<event>: %s 与 %s 不能同时出现" % (a, b))


def evaluate(on_spec, event: dict):
    """返回 ``(runs, reason)``。event 形如
    ``{"name","ref","ref_type","types","paths","commit_count","file_count"}``。"""
    if isinstance(on_spec, str):
        spec = {on_spec: None}
    elif isinstance(on_spec, (list, tuple)):
        spec = {e: None for e in on_spec}
    else:
        spec = dict(on_spec)
    name = event["name"]
    if name not in spec:
        return False, "事件 %s 未在 on: 中声明" % name
    return _one_event(name, dict(spec[name] or {}), event)


def _one_event(name: str, filt: dict, event: dict):
    types = filt.get("types")
    if types and event.get("types") and not (set(types) & set(event["types"])):
        return False, "types 与事件活动类型无交集"

    if name not in REF_EVENTS:
        return True, "该事件无 ref/路径过滤器, 直接运行"

    is_tag = event.get("ref_type") == "tag"
    ref = event.get("ref", "")

    if is_tag:
        if ("branches" in filt or "branches-ignore" in filt) and \
                not ("tags" in filt or "tags-ignore" in filt):
            return False, "只声明了 branches*, 标签推送不触发"
        if "tags" in filt and "tags-ignore" in filt:
            raise ValueError("tags 与 tags-ignore 不能同时出现")
        if "tags" in filt and not match_ordered(filt["tags"], ref):
            return False, "tags 模式未命中"
        if "tags-ignore" in filt and match_any(filt["tags-ignore"], ref):
            return False, "命中 tags-ignore"
        return True, "标签推送通过;路径过滤不适用于标签"

    if "branches" in filt and "branches-ignore" in filt:
        raise ValueError("branches 与 branches-ignore 不能同时出现")
    if ("tags" in filt or "tags-ignore" in filt) and \
            not ("branches" in filt or "branches-ignore" in filt):
        return False, "只声明了 tags*, 分支推送不触发"
    if "branches" in filt and not match_ordered(filt["branches"], ref):
        return False, "branches 模式未命中"
    if "branches-ignore" in filt and match_any(filt["branches-ignore"], ref):
        return False, "命中 branches-ignore"

    if "paths" in filt and "paths-ignore" in filt:
        raise ValueError("paths 与 paths-ignore 不能同时出现")
    if "paths" not in filt and "paths-ignore" not in filt:
        return True, "无路径过滤"
    if event.get("commit_count", 0) > 1000:
        return True, "提交数 > 1000 -> 总是运行(官方行为, 绕过路径过滤)"

    changed = list(event.get("paths") or [])
    if "paths-ignore" in filt:
        if changed and all(match_any(filt["paths-ignore"], p) for p in changed):
            return False, "全部变更路径命中 paths-ignore"
        return True, "存在未命中 paths-ignore 的路径"
    if any(match_ordered(filt["paths"], p) for p in changed):
        return True, "存在命中 paths 的路径"
    if event.get("file_count", 0) > 3000:
        return True, "变更文件 > 3000 且匹配项可能不在前 3000 个 -> 保守判运行"
    return False, "无路径命中 paths(且未超 3000 文件)"


# ------------------------------------------------------------------ matrix

def expand_matrix(matrix: dict) -> list:
    """按官方 include/exclude 语义展开矩阵, 返回按声明顺序排列的组合字典列表。"""
    if "exclude" in matrix and "include" in matrix and not matrix.get("include"):
        pass                                    # include: [] 合法且无作用
    axes = {k: v for k, v in matrix.items() if k not in ("include", "exclude")}
    names = list(axes)
    combos = [dict(zip(names, vals)) for vals in itertools.product(*axes.values())]
    origins = [dict(c) for c in combos]          # 每个组合对应的"原始矩阵值"

    for ex in matrix.get("exclude", []) or []:
        keep = []
        for c, o in zip(combos, origins):
            if all(o.get(k) == v for k, v in ex.items()):   # exclude 按原始值匹配
                continue
            keep.append((c, o))
        combos = [c for c, _ in keep]
        origins = [o for _, o in keep]

    for inc in matrix.get("include", []) or []:
        if not inc:
            continue
        applied = False
        for c, o in zip(combos, origins):        # 只并入"原始组合", 不动 include 新建的
            if o is None:
                # include 新建的组合不再被后续 include 合并(官方示例里两条
                # {fruit: banana} 各自成为独立组合可证)
                continue
            if all(k not in o or o[k] == v for k, v in inc.items()):
                c.update(inc)                    # 新增键可被后续 include 覆盖
                applied = True
        if not applied:
            combos.append(dict(inc))
            origins.append(None)
    return combos
