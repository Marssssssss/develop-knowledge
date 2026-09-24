"""MacroSystem 的注册与查找:转写自 MacroSystem.swift 的 MacroSystem 与
attachedMacroReference,以及 MacroExpansion.swift 的 inferFreestandingMacroRole
与 PrependLexicalContextWrapperContext。"""

from macro_const import (PROTOCOL_NAME, FREESTANDING_ROLE_ORDER,
                         no_freestanding_macro_roles)


class MacroSystemError(Exception):
    """MacroSystemError.alreadyDefined:同名宏重复注册。"""

    def __init__(self, new, existing):
        super().__init__("alreadyDefined")
        self.new = new
        self.existing = existing


class MacroSpec:
    """一条宏注册项:type 是宏实现类型,moduleName 可选,inheritedTypeList 是协议列表。"""

    def __init__(self, type_name, module_name=None, inherited_type_list=None):
        self.type = type_name
        self.module_name = module_name
        self.inherited_type_list = inherited_type_list or []


class MacroSystem:
    """var macros: [String: MacroSpec]。"""

    def __init__(self):
        self.macros = {}

    def add(self, spec, name):
        """同名已存在就抛 alreadyDefined,已注册的那条保持不变。"""
        if name in self.macros:
            raise MacroSystemError(spec.type, self.macros[name].type)
        self.macros[name] = spec

    def lookup(self, macro_name, module_name=None):
        """名字命中后还要校验模块:调用方指定了模块而注册项不是该模块 -> nil。"""
        spec = self.macros.get(macro_name)
        if spec is None:
            return None
        if module_name is not None and spec.module_name != module_name:
            return None
        return spec


def attached_macro_reference(text):
    """复刻 attachedMacroReference:解析 @Name、@Module.Name、@Module::Name。

    MemberTypeSyntax 分支要求:自身没有 moduleSelector,基类型是 IdentifierType
    且基类型既没有 moduleSelector 也没有泛型参数。因此带泛型或三段式都返回 None。
    """
    if "<" in text or ">" in text:
        return None
    if "::" in text:
        parts = text.split("::")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            return None
        return (parts[1], parts[0])
    segs = text.split(".")
    if len(segs) == 1:
        if not segs[0]:
            return None
        return (segs[0], None)
    if len(segs) == 2:
        if not segs[0] or not segs[1]:
            return None
        return (segs[1], segs[0])
    return None


def infer_freestanding_macro_role(conforms_to):
    """inferFreestandingMacroRole:按 expression -> declaration -> codeItem 试。

    conforms_to 是宏实现类型已遵循的协议集合(用角色名表示)。
    """
    for role in FREESTANDING_ROLE_ORDER:
        if role in conforms_to:
            return role
    raise ValueError(no_freestanding_macro_roles("macro"))


class Context:
    """最小的 MacroExpansionContext:只实现 makeUniqueName 计数与诊断收集。"""

    def __init__(self, lexical_context=None):
        self.lexical_context = list(lexical_context or [])
        self.unique_counter = 0
        self.diagnostics = []

    def make_unique_name(self, name):
        self.unique_counter += 1
        return "%s_%d" % (name, self.unique_counter)

    def diagnose(self, message):
        self.diagnostics.append(message)


class PrependLexicalContextWrapperContext:
    """PrependLexicalContextWrapperContext:只在 lexicalContext 前面追加节点。"""

    def __init__(self, prepend_lexical_context, wrapped):
        self.prepend_lexical_context = list(prepend_lexical_context)
        self.wrapped_context = wrapped

    @property
    def lexical_context(self):
        return self.prepend_lexical_context + self.wrapped_context.lexical_context

    def make_unique_name(self, name):
        """直接转发给被包装的 context —— 唯一名不会被外层 lexical context 区分。"""
        return self.wrapped_context.make_unique_name(name)

    def diagnose(self, message):
        self.wrapped_context.diagnose(message)
