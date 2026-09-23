"""SLEIGH 位模式的解析与求值（从 sleigh.py 拆出，保持单文件 <= 300 行）。

语法子集（对应 sleigh_constructors.html §7.4）：
    pattern := or
    or      := and ( "|" and )*
    and     := primary ( "&" primary | "..." [ "&" primary ] )*
    primary := "(" or ")" | "..." | ident "=" number | ident
"""

import re


# ---------------------------------------------------------------- 位模式求值

def parse_pattern(text, spec):
    """解析 `a=1 & b | (c=2 & d=3) & OP1` 形式的位模式。"""
    tokens = re.findall(r"0[xX][0-9A-Fa-f]+|[A-Za-z_][A-Za-z_0-9]*|\d+|\.\.\.|&|\||\(|\)|=", text)
    pos = [0]

    def peek():
        return tokens[pos[0]] if pos[0] < len(tokens) else None

    def take():
        t = peek()
        pos[0] += 1
        return t

    def parse_or():
        node = parse_and()
        while peek() == "|":
            take()
            node = ("or", node, parse_and())
        return node

    def parse_and():
        node = parse_primary()
        # `...` 在真实规范里出现在两个 token 之间（如 `:ADC OP1 is (...) ... & OP1`），
        # 语法上等价于分隔符
        while True:
            if peek() == "&":
                take()
                node = ("and", node, parse_primary())
            elif peek() == "...":
                take()
                node = ("and", node, ("ellipsis",))
                # `...` 之后可以继续用 `&` 挂操作数（如 `... & OP1`）
                if peek() == "&":
                    take()
                    node = ("and", node, parse_primary())
            else:
                break
        return node

    def parse_primary():
        t = take()
        if t == "(":
            node = parse_or()
            if take() != ")":
                raise ValueError("缺右括号")
            return node
        if t == "...":
            return ("ellipsis",)
        nxt = peek()
        if nxt == "=":
            take()
            val = take()
            return ("constraint", t, int(val, 0))
        return ("operand", t)

    node = parse_or()
    if pos[0] != len(tokens):
        raise ValueError("位模式解析到一半就停了: %r" % (tokens[pos[0]:],))
    return node


def eval_pattern(node, fields, spec, operands):
    """fields: {字段名: 值}；operands: 收集到的操作数标识。"""
    kind = node[0]
    if kind == "and":
        return eval_pattern(node[1], fields, spec, operands) and \
               eval_pattern(node[2], fields, spec, operands)
    if kind == "or":
        return eval_pattern(node[1], fields, spec, operands) or \
               eval_pattern(node[2], fields, spec, operands)
    if kind == "ellipsis":
        return True
    if kind == "constraint":
        name, val = node[1], node[2]
        if name not in fields:
            raise KeyError("位模式里的 %s 不是已知字段" % name)
        # 约束用的是字段的原始整数编码，attach 之后的含义在这里不适用
        return fields[name] == val
    if kind == "operand":
        operands.append(node[1])
        return node[1] in fields or node[1] in spec.tokens or \
            any(x.table == node[1] for x in spec.constructors)
    raise ValueError("unknown pattern node %s" % kind)


