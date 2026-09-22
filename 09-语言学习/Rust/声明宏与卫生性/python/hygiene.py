"""`macro_rules!` 的卫生性与作用域（依据 Rust Reference `macros-by-example`）。

官方原文（Hygiene）：**mixed-site hygiene** ——

- *loop labels*、*block labels*、*local variables* 在**定义处**查找；
- 其它符号（item、函数、类型……）在**调用处**查找；
- 宏展开里定义的 label / 局部变量在不同次调用之间**不共享**（E0425）；
- `$crate` 指代「定义这个宏的 crate」，且**不改变可见性**规则。

作用域（Scoping）官方原文：

- 宏有两种作用域：**textual scope**（按源码出现顺序，可跨模块跨文件）与
  **path-based scope**（与 item 作用域一致）；
- 无修饰调用**先**查 textual，再查 path-based；带路径的调用只查 path-based；
- 重复定义不是错误，后定义的**遮蔽**先定义的；
- textual 的绑定**遮蔽** path-based 的绑定。
"""

from __future__ import annotations


class Unresolved(Exception):
    def __init__(self, code, detail):
        super().__init__("%s: %s" % (code, detail))
        self.code = code
        self.detail = detail


class Macro:
    def __init__(self, name, rules, defining_crate="crate", visibility="private"):
        self.name = name
        self.rules = rules          # [(matcher_src, transcriber_src), ...]
        self.defining_crate = defining_crate
        self.visibility = visibility
        self.invocation_id = 0

    def fresh_ctx(self):
        """每次调用都换一个 syntax context —— 这是「不共享局部变量」的实现。"""
        self.invocation_id += 1
        return "ctx%d" % self.invocation_id


# ------------------------------------------------------------------ 卫生性
LOCAL_KINDS = {"local", "label"}


def resolve_symbol(name, kind, def_site, call_site, ctx, bindings_introduced):
    """按 mixed-site hygiene 决定一个符号该在哪里查找。

    kind 属于 LOCAL_KINDS（局部变量 / 标签）→ 定义处查找；
    否则（item / 函数 / 类型）→ 调用处查找。
    """
    if kind in LOCAL_KINDS:
        if (name, ctx) in bindings_introduced:
            return ("def", bindings_introduced[(name, ctx)])
        if name in def_site.get(kind, {}):
            return ("def", def_site[kind][name])
        raise Unresolved("E0425", "cannot find value `%s` in this scope "
                                  "(宏展开里定义的局部变量不在调用间共享)" % name)
    if name in call_site.get(kind, {}):
        return ("call", call_site[kind][name])
    if name in def_site.get(kind, {}):
        return ("def", def_site[kind][name])
    raise Unresolved("E0425", "cannot find `%s` in this scope" % name)


def resolve_path_via_crate(path, macro, call_crate, visible_items):
    """`$crate::x` 指定义宏的 crate；可见性规则照旧生效。"""
    if not path.startswith("$crate"):
        return ("call", path)
    target = macro.defining_crate + path[len("$crate"):]
    if macro.visibility == "private" and call_crate != macro.defining_crate:
        raise Unresolved("E0603", "%s 在调用处不可见（$crate 不绕过可见性）" % target)
    if target not in visible_items:
        raise Unresolved("E0425", "cannot find %s" % target)
    return ("def", target)


# ------------------------------------------------------------------ 作用域
class Scope:
    """一条链上的宏绑定；`order` 用来表达 textual scope 的「先后顺序」。"""

    def __init__(self):
        self.textual = []       # [(order, name, macro)]
        self.path_based = {}    # name -> macro
        self.counter = 0

    def define_textual(self, name, macro, module="root"):
        self.counter += 1
        entry = (self.counter, name, macro, module)
        self.textual.append(entry)
        return self.counter

    def define_path(self, name, macro):
        self.path_based[name] = macro

    def lookup(self, name, at_order=None, qualified=False, module="root"):
        if qualified:
            return self.path_based.get(name)
        limit = at_order if at_order is not None else self.counter + 1
        # textual：只看「出现在调用点之前」的定义，取最近的一个，可跨模块
        cands = [e for e in self.textual
                 if e[1] == name and e[0] < limit
                 and (e[3] == module or e[3] == "root" or module.startswith(e[3]))]
        if cands:
            return max(cands, key=lambda e: e[0])[2]
        return self.path_based.get(name)
