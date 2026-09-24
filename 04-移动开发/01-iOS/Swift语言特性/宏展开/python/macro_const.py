"""常量:来自 swift-syntax 的 Sources/SwiftSyntaxMacroExpansion/MacroExpansion.swift。"""

# MacroRole 的十二个角色
EXPRESSION = "expression"
DECLARATION = "declaration"
ACCESSOR = "accessor"
MEMBER_ATTRIBUTE = "memberAttribute"
MEMBER = "member"
PEER = "peer"
CONFORMANCE = "conformance"
CODE_ITEM = "codeItem"
EXTENSION = "extension"
PREAMBLE = "preamble"
BODY = "body"

# MacroRole.protocolName
PROTOCOL_NAME = {
    EXPRESSION: "ExpressionMacro",
    DECLARATION: "DeclarationMacro",
    ACCESSOR: "AccessorMacro",
    MEMBER_ATTRIBUTE: "MemberAttributeMacro",
    MEMBER: "MemberMacro",
    PEER: "PeerMacro",
    CONFORMANCE: "ConformanceMacro",
    CODE_ITEM: "CodeItemMacro",
    EXTENSION: "ExtensionMacro",
    PREAMBLE: "PreambleMacro",
    BODY: "BodyMacro",
}

# inferFreestandingMacroRole 的判定顺序:expression -> declaration -> codeItem
FREESTANDING_ROLE_ORDER = [EXPRESSION, DECLARATION, CODE_ITEM]

# 独立宏只有三种角色;其余都是附着宏
FREESTANDING_ROLES = {EXPRESSION, DECLARATION, CODE_ITEM}
ATTACHED_ROLES = set(PROTOCOL_NAME) - FREESTANDING_ROLES

DEFAULT_INDENT = 4


def unmatched_macro_role(type_name, role):
    return ("macro implementation type '%s' doesn't conform to required protocol '%s'"
            % (type_name, PROTOCOL_NAME[role]))


def no_freestanding_macro_roles(type_name):
    return ("macro implementation type '%s' does not conform to any freestanding macro protocol"
            % type_name)


def recursive_expansion(type_name):
    return type_name
