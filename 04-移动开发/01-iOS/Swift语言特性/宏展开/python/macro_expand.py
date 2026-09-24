"""展开结果的合并与独立宏的递归检测。

转写自 MacroExpansion.swift 的 collapse(...) 与 MacroSystem.swift 的
expandFreestandingMacro / MacroExpansion.withExpandedNode。
"""

from macro_const import (ACCESSOR, MEMBER_ATTRIBUTE, BODY, PREAMBLE, DEFAULT_INDENT)

NOT_A_MACRO = "notAMacro"
FAILURE = "failure"
SUCCESS = "success"


def collapse(expansions, role, declaration_has_accessor=False,
             indentation_width=DEFAULT_INDENT):
    """collapse:按角色决定分隔符,必要时把结果包进一对花括号。"""
    if not expansions:
        return ""
    exps = list(expansions)
    separator = "\n\n"

    def wrap_in_braces():
        pad = " " * indentation_width
        # indented(by:) 是每一行都加缩进,首行也不例外
        exps[:] = ["\n".join(pad + line for line in e.split("\n")) for e in exps]
        exps[0] = "{\n" + exps[0]
        exps[-1] = exps[-1] + "\n}"
        return "\n"

    if role == ACCESSOR:
        # 只有"声明本身没有 accessorBlock"时才需要补一对花括号
        if not declaration_has_accessor:
            separator = wrap_in_braces()
    elif role == MEMBER_ATTRIBUTE:
        separator = " "
    elif role == BODY:
        separator = wrap_in_braces()
    elif role == PREAMBLE:
        separator = "\n"

    collapsed = ""
    for e in exps:
        # 展开结果已经以分隔符开头时不重复加
        if collapsed == "" or e.startswith(separator):
            collapsed += e
        else:
            collapsed += separator + e
    return collapsed


class MacroApplication:
    """MacroApplication 的最小等价物:维护正在展开的独立宏栈以检测递归。"""

    def __init__(self, system, context):
        self.system = system
        self.context = context
        self.expanding = []

    def expand_freestanding(self, macro_name, module_name, expand_macro):
        """返回 (状态, 展开结果)。expand_macro 拿到宏名后可自行再触发嵌套展开。"""
        spec = self.system.lookup(macro_name, module_name)
        if spec is None:
            return (NOT_A_MACRO, None)
        if any(m == spec.type for m in self.expanding):
            self.context.diagnose("recursiveExpansion(%s)" % spec.type)
            return (FAILURE, None)
        self.expanding.append(spec.type)
        try:
            try:
                expanded = expand_macro(spec.type, macro_name)
            except Exception as exc:      # noqa: BLE001 - 与源码一致,吞掉并转成诊断
                self.context.diagnose(str(exc))
                return (FAILURE, None)
            if expanded is None:
                return (FAILURE, None)
            return (SUCCESS, expanded)
        finally:
            self.expanding.pop()

    def with_expanded_node(self, macro_type, body):
        """withExpandedNode:push 与 pop 精确包住 body,body 里才允许继续展开。"""
        self.expanding.append(macro_type)
        try:
            return body()
        finally:
            self.expanding.pop()

    def expand_attached(self, decl_name, attributes, role, expand_one):
        """expandMacros:逐个属性尝试,抛错的那个只记诊断,不影响其余属性。"""
        results = []
        for attr in attributes:
            spec = self.system.lookup(attr["name"], attr.get("module"))
            if spec is None:
                continue
            # macroAttributes(ofType:) 只保留"实现类型遵循该角色协议"的那些
            if role not in attr.get("conforms_to", set()):
                continue
            try:
                got = expand_one(spec.type, attr)
            except Exception as exc:      # noqa: BLE001
                self.context.diagnose(str(exc))
                continue
            if got is not None:
                results.extend(got)
        return results
