# -*- coding: utf-8 -*-
"""
ES modules 最小 loader：Link / Evaluate 两阶段 + 实时绑定（live binding）。

权威依据：ECMA-262「Modules」章（实读 tc39.es/ecma262 的 scripts-and-modules 页）
  - Cyclic Module Record 状态机：unlinked → linking → linked → evaluating → evaluated
  - InnerModuleLinking 用 DFS + stack 遍历，"to avoid infinite loops with circular
    dependencies"；InitializeEnvironment 期间建立绑定
  - InitializeEnvironment：函数声明在此阶段即完成初始化（这就是提升的来源），
    let/const 只 CreateMutableBinding 而不 InitializeBinding ⇒ TDZ
  - InnerModuleEvaluation(module, stack, index)："A module is evaluating while it is
    being traversed"；依赖先求值（后序）
  - 求值错误传播（规范示例）：A←B←C 中 B 抛错 → "the exception will be recorded in both
    A and B's [[EvaluationError]] fields … C will also become evaluated but, in contrast
    to A and B, will remain without an [[EvaluationError]]"
  - 链接错误（规范示例）：A 从 C import 一个不存在的绑定 → A、B 变 unlinked，C 保持 linked
  - ResolveExport 返回 ResolvedBinding Record {[[Module]], [[BindingName]]} / null /
    ambiguous；resolveSet 用于检测 export * 循环与歧义
"""

LINK_STATES = ("linking", "linked", "evaluating", "evaluated")


class Binding:
    """一个环境记录槽位。import 拿到的就是**同一个** Binding 对象 ⇒ live binding。"""

    __slots__ = ("value", "initialized", "mutable")

    def __init__(self, mutable=True):
        self.value = None
        self.initialized = False
        self.mutable = mutable

    def get(self):
        if not self.initialized:
            raise ReferenceError("Cannot access '%s' before initialization" % "binding")
        return self.value

    def set(self, v):
        if not self.mutable:
            raise TypeError("Assignment to constant or imported binding")
        self.value = v
        self.initialized = True


class ImportBinding:
    """导入侧的视图：读穿透到导出模块的 Binding（live），写一律 TypeError。"""

    __slots__ = ("target",)

    def __init__(self, target):
        self.target = target

    def get(self):
        return self.target.get()

    def set(self, v):
        raise TypeError("Assignment to imported binding")

    @property
    def initialized(self):
        return self.target.initialized


class Module:
    """模块源码：imports=[(模块名,[名字])]，body=[(kind, 名字, 可执行)]，exports/star_exports。"""

    def __init__(self, name, imports=None, body=None, exports=None,
                 star_exports=None, throws=None):
        self.name = name
        self.imports = list(imports or [])
        self.body = list(body or [])
        self.exports = list(exports or [])
        self.star_exports = list(star_exports or [])
        self.throws = throws


class ModuleRecord:
    def __init__(self, mod):
        self.mod = mod
        self.status = "unlinked"
        self.env = {}
        self.evaluation_error = None
        self.exports_map = {}

    def resolve_export(self, name, resolve_set=None):
        """ResolveExport：返回 Binding / None（找不到或循环）/ "ambiguous"。"""
        if resolve_set is None:
            resolve_set = set()
        key = (self.mod.name, name)
        if key in resolve_set:
            return None                       # 循环 import/export 路径
        resolve_set = resolve_set | {key}
        if name in self.exports_map:
            return self.exports_map[name]
        found = None
        for star in self.mod.star_exports:
            r = self._loader.records[star].resolve_export(name, resolve_set)
            if r == "ambiguous":
                return "ambiguous"
            if r is None:
                continue
            if found is not None and found is not r:
                return "ambiguous"
            found = r
        return found


class Loader:
    def __init__(self, modules):
        self.records = {}
        for m in modules:
            rec = ModuleRecord(m)
            rec._loader = self
            self.records[m.name] = rec
        self.link_order = []
        self.eval_order = []

    # ---------------- Link ----------------
    def link(self, entry):
        self._link(self.records[entry], [])
        return self

    def _link(self, rec, stack):
        if rec.status in LINK_STATES:
            return
        rec.status = "linking"
        stack.append(rec)
        # 导出表必须**先于**依赖链接建立：循环依赖时对方要能在自己尚未求值前拿到 Binding
        self._create_local_bindings(rec)
        try:
            # export * 同样产生依赖（规范里会进 [[RequestedModules]]）
            for dep_name in list(rec.mod.star_exports):
                self._link(self.records[dep_name], stack)
            for dep_name, names in rec.mod.imports:
                if dep_name not in self.records:
                    raise SyntaxError("Cannot resolve module '%s'" % dep_name)
                self._link(self.records[dep_name], stack)
                self._resolve_imports(rec, dep_name, names)
        except SyntaxError:
            for r in stack:
                r.status = "unlinked"         # 规范：栈上模块回滚，已 linked 的依赖不受影响
            raise
        rec.status = "linked"
        stack.pop()
        self.link_order.append(rec.mod.name)

    def _create_local_bindings(self, rec):
        """InitializeEnvironment 第 1 步：本地绑定 + 函数声明提升 + 导出表。"""
        for kind, name, fn in rec.mod.body:
            b = Binding(mutable=(kind == "let"))
            rec.env[name] = b
            if kind == "function":
                b.value = fn
                b.initialized = True
        for n in rec.mod.exports:
            if n not in rec.env:
                raise SyntaxError("Local export '%s' is not declared" % n)
            rec.exports_map[n] = rec.env[n]

    def _resolve_imports(self, rec, dep_name, names):
        """import 拿到的是导出模块的**同一个 Binding 对象**，且不可写。"""
        dep = self.records[dep_name]
        for n in names:
            got = dep.resolve_export(n)
            if got is None or got == "ambiguous":
                raise SyntaxError(
                    "The requested module '%s' does not provide an export named '%s'"
                    % (dep_name, n))
            rec.env[n] = ImportBinding(got)

    # ---------------- Evaluate ----------------
    def evaluate(self, entry):
        try:
            self._eval(self.records[entry], [])
        except Exception as e:                 # 含 ReferenceError / RuntimeError
            pass
        return self

    def _eval(self, rec, stack):
        if rec.status in ("evaluating", "evaluated"):
            return
        rec.status = "evaluating"
        stack.append(rec)
        for dep_name, _names in rec.mod.imports:
            self._eval(self.records[dep_name], stack)
        try:
            if rec.mod.throws:
                raise rec.mod.throws
            for kind, name, fn in rec.mod.body:
                if kind == "function":
                    continue                   # 提升阶段已完成
                fn(self, rec)
        except Exception as e:
            # 规范：异常记录到「当前仍在 evaluating 的栈上模块」
            for r in stack:
                r.evaluation_error = e
                r.status = "evaluated"
            raise
        rec.status = "evaluated"
        stack.pop()
        self.eval_order.append(rec.mod.name)

    # ---------------- 辅助 ----------------
    def get(self, rec_name, binding_name):
        return self.records[rec_name].env[binding_name].get()

    def set(self, rec_name, binding_name, value):
        self.records[rec_name].env[binding_name].set(value)
