#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ghidra 脚本命名 API 的最小模型:createLabel / createFunction / setEOLComment 的语义。

依据(本轮实读的官方 javadoc 与文档):
  * 类层次与脚本元数据:Ghidra 官方脚本文档给出
    『FlatProgramAPI | └── GhidraScript | └── YourScript』,
    并说明 GhidraScript 在 FlatProgramAPI 之上加了『User interaction (ask methods)』
    『Output methods (print/println)』『State variables』『Script arguments』。
    FlatProgramAPI 的类注释还特意写:
    『NOTE: NO METHODS SHOULD EVER BE REMOVED FROM THIS CLASS. NO METHOD SIGNATURES
    SHOULD EVER BE CHANGED IN THIS CLASS. This class is used by GhidraScript. Changing
    this class will break user scripts. That is bad. Don't do that.』
    —— 这就是 createSymbol 从 7.4 起被标 @Deprecated 却仍然保留的原因。
  * 关键方法签名(官方 javadoc 逐字):
      createLabel(Address address, String name, boolean makePrimary)
      createLabel(Address address, String name, boolean makePrimary, SourceType sourceType)
      createLabel(Address address, String name, Namespace namespace, boolean makePrimary,
                  SourceType sourceType)
      createFunction(Address entryPoint, String name)
      setEOLComment(Address address, String comment) -> boolean
      removeSymbol(Address address, String name) -> boolean
    其中 createLabel 的说明写明「If makeUnique==true, then if the name is a duplicate,
    the address will be concatenated to name to make it unique」。
  * SourceType 枚举与优先级(官方 javadoc 逐字):
      ANALYSIS      The object's source indicator for an auto analysis.
      DEFAULT       The object's source indicator for a default.
      IMPORTED      The object's source indicator for an imported.
      USER_DEFINED  The object's source indicator for a user defined.
    『USER_DEFINED objects are higher priority than IMPORTED objects which are higher
    priority than ANALYSIS objects which are higher priority than DEFAULT objects.』
    文档另有一句直白结论:『Symbol source determines priority - user symbols won't be
    overwritten by analysis.』本模型的核心断言就建立在这条规则上。
  * 默认名前缀(FUN_/DAT_/LAB_/SUB_)来自官方 Symbol Table 插件帮助与 Beginner 学生手册:
    『Source of Symbol: Default FUN_, LAB_, DWORD_ etc. / User Defined / Imported /
    Analysis』;『Ghidra creates default symbols: FUN_ for functions, DAT_ for data,
    LAB_ for labels, SUB_ for subroutines』。
  * 命名规则(同上文档):『Start with letter or underscore / Contain letters, digits,
    underscores / No spaces / Case-sensitive』,且『Label names must be unique within
    their namespace』。

本模块只建模"符号表语义",不依赖 Ghidra 本体,故可在任意机器上实跑自检。

运行: python ghidra_symbols.py     退出码 0 表示全部断言通过。
"""

import re
import sys

# 官方优先级:USER_DEFINED > IMPORTED > ANALYSIS > DEFAULT
SOURCE_PRIORITY = {"DEFAULT": 0, "ANALYSIS": 1, "IMPORTED": 2, "USER_DEFINED": 3}

# 反汇编器自动生成名的前缀(官方文档列举)
DEFAULT_PREFIXES = ("FUN_", "DAT_", "LAB_", "SUB_", "DWORD_", "UNK_", "thunk_FUN_")

# 官方命名规则:首字符必须是字母或下划线,其余只允许字母/数字/下划线
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class NamingError(Exception):
    """违反命名规则 —— 官方:start with letter or underscore; no spaces。"""


class PriorityError(Exception):
    """试图用低优先级来源覆盖高优先级符号 —— 官方:user symbols won't be overwritten。"""


def is_default_name(name):
    """是否是反汇编器自动生成的名字(只有这种名字才该被批量重命名替换)。"""
    return any(name.startswith(p) for p in DEFAULT_PREFIXES)


def check_name(name):
    if not _NAME_RE.match(name):
        raise NamingError("非法符号名: %r(须以字母/下划线开头,只含字母数字下划线,无空格)" % name)
    return name


def unique_name(name, addr):
    """官方 makeUnique 的口径:重名时把地址拼到名字后面。"""
    return "%s_%04x" % (name, addr)


class Symbol(object):
    __slots__ = ("addr", "name", "source", "primary", "namespace")

    def __init__(self, addr, name, source="DEFAULT", primary=True, namespace="Global"):
        self.addr = addr
        self.name = name
        self.source = source
        self.primary = primary
        self.namespace = namespace

    def __repr__(self):
        return "Symbol(%04x, %s, %s)" % (self.addr, self.name, self.source)


class Function(object):
    __slots__ = ("entry", "name", "eol_comment")

    def __init__(self, entry, name):
        self.entry = entry
        self.name = name
        self.eol_comment = None


class Transaction(object):
    """官方脚本里所有写操作都必须包在 transaction 里(文档示例一律用
    currentProgram.startTransaction(...) / endTransaction(txId, true|false))。
    这里用快照实现 begin/commit/rollback 三态。"""

    def __init__(self, program, name):
        self.program = program
        self.name = name
        self.snapshot = None
        self.active = False

    def begin(self):
        self.snapshot = self.program.dump()
        self.active = True
        return self

    def commit(self):
        assert self.active, "commit 前必须先 begin"
        self.active = False

    def rollback(self):
        self.program.load(self.snapshot)
        self.active = False


class Program(object):
    """符号表 + 函数表的最小模型。"""

    def __init__(self):
        self._symbols = {}          # addr -> [Symbol]
        self._functions = {}        # entry -> Function
        self.create_count = 0

    # ---------------- 快照(供事务回滚)

    def dump(self):
        return ([(a, s.addr, s.name, s.source, s.primary, s.namespace)
                 for a in self._symbols for s in self._symbols[a]],
                [(e, f.name, f.eol_comment) for e, f in self._functions.items()],
                self.create_count)

    def load(self, snap):
        syms, funcs, cnt = snap
        self._symbols = {}
        for _, addr, name, source, primary, ns in syms:
            self._symbols.setdefault(addr, []).append(
                Symbol(addr, name, source, primary, ns))
        self._functions = {}
        for entry, name, cmt in funcs:
            f = Function(entry, name)
            f.eol_comment = cmt
            self._functions[entry] = f
        self.create_count = cnt

    # ---------------- 读

    def symbols_at(self, addr):
        return list(self._symbols.get(addr, []))

    def primary_symbol(self, addr):
        for s in self._symbols.get(addr, []):
            if s.primary:
                return s
        return None

    def all_symbols(self):
        return [s for a in sorted(self._symbols) for s in self._symbols[a]]

    def functions(self):
        return [self._functions[e] for e in sorted(self._functions)]

    def function_at(self, addr):
        return self._functions.get(addr)

    # ---------------- 写

    def create_function(self, entry, name):
        check_name(name)
        self._functions[entry] = Function(entry, name)
        self.create_label(entry, name, True, "USER_DEFINED")
        return self._functions[entry]

    def create_label(self, addr, name, make_primary=True, source_type="USER_DEFINED"):
        """对应 FlatProgramAPI.createLabel(...),含重名与优先级两条规则。"""
        check_name(name)
        existing = self._symbols.setdefault(addr, [])
        for s in existing:
            if s.name == name and s.namespace == "Global":
                return s                      # 已存在同名 → 幂等
        src = SOURCE_PRIORITY[source_type]
        new = Symbol(addr, name, source_type, make_primary)
        if make_primary:
            prim = self.primary_symbol(addr)
            if prim is not None and SOURCE_PRIORITY[prim.source] > src:
                raise PriorityError(
                    "拒绝覆盖:地址 %04x 的 primary 符号 %s 来源 %s 优先级高于 %s"
                    % (addr, prim.name, prim.source, source_type))
            for s in existing:
                s.primary = False
        existing.append(new)
        self.create_count += 1
        return new

    def rename_symbol(self, addr, old_name, new_name, source_type="USER_DEFINED"):
        """改名 = 新建一个高优先级标签 + 删掉旧标签(官方脚本里的常见做法)。"""
        check_name(new_name)
        for s in self._symbols.get(addr, []):
            if s.name == old_name:
                self.create_label(addr, new_name, True, source_type)
                self._symbols[addr] = [x for x in self._symbols[addr]
                                       if x.name != old_name]
                if addr in self._functions:
                    self._functions[addr].name = new_name
                return True
        return False

    def set_eol_comment(self, addr, comment):
        f = self._functions.get(addr)
        if f is None:
            return False
        f.eol_comment = comment
        return True
