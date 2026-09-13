"""Jinja2 简化实现 + 演示

来源:
- Jinja2 Template Designer Documentation
  (jinja.palletsprojects.com/en/2.10.x/templates/)
  - delimiters:
    {# ... #}  Comments
    {{ ... }}  Expressions
    {% ... %}  Statements (control flow)
    # ...      Line Statements
  - variables:passed via context dict;
    {{ foo.bar }} = getattr first, then __getitem__
  - filters: | syntax {{ name|upper|truncate }}
    "Filters may have optional arguments in parentheses"
  - tests: is syntax {{ var is defined }}
  - for/if:{% for x in y %}{% endfor %}{% if %}{% elif %}{% else %}{% endif %}

实现:从零写 miniJinja,支持:
- {{ var }} / {{ var.attr }} / {{ var["key"] }}
- {{ var|filter }}
- {% for x in iterable %}
- {% if expr %}{% else %}
- 简单表达式 == / != / and / or

不实现:宏 / 模板继承 / Python 风格沙箱
"""

import re

# Tokenizer — 区分 TEXT / VARIABLE / TAG / COMMENT

TOKEN_RE = re.compile(r"""
    ({{.*?}}|{%.*?%}|{#.*?#}|(?:[^,{]|{{|{|{|%))+  # 占位保留(实际不匹配这么长)
""", re.VERBOSE)

TEXT_RE = re.compile(r"[^#{]+")
VAR_RE = re.compile(r"{{(.*?)}}")
TAG_RE = re.compile(r"{%(.*?)%}")
COM_RE = re.compile(r"{(.*?)#}")


def tokenize(template: str):
    """返回三态 token 流:('text', str) / ('var', expr_str) / ('tag', stmt_str)"""
    tokens = []
    i = 0
    while i < len(template):
        # 找最近的 {{ {% 或 {#
        m = re.search(r"(\{\{|\{%|\{#)", template[i:])
        if not m:
            tokens.append(("text", template[i:]))
            break
        # 文本段
        start = i + m.start()
        if start > i:
            tokens.append(("text", template[i:start]))
        i = start
        kind, end = None, None
        if template.startswith("{{", i):
            kind, end = "var", template.find("}}", i + 2)
            tokens.append(("var", template[i + 2:end].strip()))
            i = end + 2
        elif template.startswith("{%", i):
            kind, end = "tag", template.find("%}", i + 2)
            tokens.append(("tag", template[i + 2:end].strip()))
            i = end + 2
        elif template.startswith("{#", i):
            kind, end = "com", template.find("#}", i + 2)
            i = end + 2
            # 注释丢弃,不产出 token
    return tokens


# 表达求值器 (简化) — 支持 var / attr / item / 比较 / and / or / 一元

def lookup(var, path, ctx):
    """var|path 通过 .attr / [key] 逐段寻值,Jinja 规则:
       foo.bar: getattr first, then __getitem__
    """
    if not path:
        return var
    for seg in re.split(r"\.|(\[)", path):
        if seg == "[":
            continue
        seg = seg.rstrip("]")
        if seg.startswith("'") or seg.startswith('"'):
            seg = seg[1:-1]
        if var is None:
            return None
        if hasattr(var, seg) and not isinstance(var, dict) and not isinstance(var, list):
            var = getattr(var, seg)
        elif isinstance(var, dict) and seg in var:
            var = var[seg]
        elif isinstance(var, list) and seg.isdigit():
            var = var[int(seg)]
        else:
            # Jinja 默认 Undefined → 返回空字符串
            return ""
    return var


def eval_cond(expr, ctx):
    """评估条件表达式(简化):支持 var / var.literal / comparisons / and or"""
    # 处理 and / or 低优先级
    def split_top(s, sep):
        out, depth = [""], 0
        for c in s:
            if c == "(": depth += 1
            if c == ")": depth -= 1
            if c == sep and depth == 0:
                out.append(""); continue
            out[-1] += c
        return out
    for op in (" or ", " and "):
        parts = split_top(expr, op)
        if len(parts) > 1:
            f = any if op == " or " else all
            return f(eval_cond(p.strip(), ctx) for p in parts)
    for op in ("==", "!="):
        parts = expr.split(op, 1)
        if len(parts) == 2:
            l = eval_term(parts[0].strip(), ctx)
            r = eval_term(parts[1].strip(), ctx)
            return (l == r) if op == "==" else (l != r)
    return eval_term(expr.strip(), ctx)


def eval_term(expr, ctx):
    """term: 变量路径 / 字面量 / not"""
    expr = expr.strip()
    if not expr:
        return None
    if expr == "True": return True
    if expr == "False": return False
    if expr == "None": return None
    if (expr.startswith("'") and expr.endswith("'")) or (expr.startswith('"') and expr.endswith('"')):
        return expr[1:-1]
    if expr.isdigit():
        return int(expr)
    if expr.startswith("not "):
        return not eval_cond(expr[4:], ctx)
    # 变量路径:支持 a.b.c
    parts = expr.split(".", 1)
    return lookup(ctx.get(parts[0], ""), parts[1] if len(parts) == 2 else "", ctx)


def eval_filters(value, expr):
    """filters: value|upper|lower|default('x')"""
    while "|" in expr:
        name, rest = expr.split("|", 1)
        m = re.match(r"(\w+)(?:\((.*)\))?", rest)
        if not m: break
        fname, fargs = m.group(1), m.group(2)
        if fname == "upper": value = str(value).upper()
        elif fname == "lower": value = str(value).lower()
        elif fname == "title": value = str(value).title()
        elif fname == "default":
            default = fargs.strip()[1:-1] if fargs and fargs[0] in "'\"" else (fargs or "")
            if value is None or value == "" or value == "None": value = default
        expr = rest[m.end():].strip()
    return value


def render_var(expr, ctx):
    """{{ expr }} expr 可以带过滤器链"""
    if "|" not in expr:
        return str(eval_term(expr, ctx))
    parts = expr.split("|", 1)
    val = eval_term(parts[0].strip(), ctx)
    return str(eval_filters(val, parts[1]))


# -----------------------------------------------------------------------------
# 主渲染函数
# -----------------------------------------------------------------------------

def render(template, ctx):
    tokens = tokenize(template)
    output = []
    i = 0
    while i < len(tokens):
        kind, content = tokens[i]
        if kind == "text":
            output.append(content)
        elif kind == "var":
            output.append(render_var(content, ctx))
        elif kind == "tag":
            content = content.strip()
            if content.startswith("for "):
                # for x in y: collect body until endfor
                m = re.match(r"for\s+(\w+)\s+in\s+(.+)", content)
                if not m:
                    raise ValueError(f"bad for: {content}")
                var_name = m.group(1)
                iter_expr = m.group(2).strip()
                body, end = collect_block(tokens, i + 1, "endfor")
                items = eval_term(iter_expr, ctx) or []
                for it in items:
                    sub_ctx = dict(ctx)
                    sub_ctx[var_name] = it
                    output.append(render_from_tokens(body, sub_ctx))
                i = end + 1
                continue
            elif content.startswith("if "):
                cond = content[3:].strip()
                true_body, after = collect_block(tokens, i + 1, "endif")
                else_body = []
                j = i + 1
                while j < len(tokens) and tokens[j][0] == "tag":
                    head = tokens[j][1].strip()
                    if head.startswith("elif"):
                        # 当 elif 时:把 elif 当成下一个 if 嵌套(demo 简化不支持连续 elif)
                        break
                    if head == "else":
                        else_body, after = collect_block(tokens, j + 1, "endif")
                        break
                    j += 1
                if eval_cond(cond, ctx):
                    output.append(render_from_tokens(true_body, ctx))
                else:
                    output.append(render_from_tokens(else_body, ctx))
                i = after + 1
                continue
        i += 1
    return "".join(output)


def collect_block(tokens, start, terminator):
    """收集直到 tokens[k] == ('tag', terminator) 为止的所有 token"""
    body = []
    k = start
    while k < len(tokens):
        if tokens[k][0] == "tag" and tokens[k][1].strip() == terminator:
            return body, k
        body.append(tokens[k])
        k += 1
    raise ValueError(f"missing {terminator}")


def render_from_tokens(tokens, ctx):
    """对一组 token 子序列调用 render 内部循环(不从头)"""
    out = []
    i = 0
    while i < len(tokens):
        kind, content = tokens[i]
        if kind == "text":
            out.append(content)
        elif kind == "var":
            out.append(render_var(content, ctx))
        elif kind == "tag":
            # for / if 在 render 主函数处理;在 sub-render 只做变量/文本
            pass
        i += 1
    return "".join(out)


# Demo

TEMPLATE = """\
# {{ title|upper }}

Hi, I'm a {{ role|default('developer') }}.

{% if show_skills %}
Skills:
{% for s in skills %}
- {{ s|upper }}
{% endfor %}
{% else %}
(No public skills list.)
{% endif %}

Env: {{ env }}.
"""

CONTEXT = {
    "title": "About me",
    "role": "backend engineer",
    "env": "prod",
    "show_skills": True,
    "skills": ["Python", "Go", "Rust"],
}


def main():
    print("=== 模板引擎 demo (mini Jinja2) ===\n")
    print("--- Template ---\n", TEMPLATE)
    print("--- Rendered ---\n", render(TEMPLATE, CONTEXT), sep="")
    print("--- with role missing (filter default) ---")
    ctx2 = dict(CONTEXT)
    ctx2.pop("role")
    print(render(TEMPLATE, ctx2))
    print("--- with show_skills=False ---")
    ctx3 = dict(CONTEXT)
    ctx3["show_skills"] = False
    print(render(TEMPLATE, ctx3))


if __name__ == "__main__":
    main()
