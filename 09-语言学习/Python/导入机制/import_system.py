#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CPython 导入系统(import machinery)教学 demo。

  §1 sys.modules 缓存:同一模块只导入一次;删缓存键/置 None 的效果
  §2 finder/loader/spec:自定义 meta path finder 记录 find_spec 调用序列
  §3 虚拟模块:用 ModuleSpec + exec_module 注入一个"没有文件"的模块
  §4 真实包结构:__spec__ / __loader__ / __package__ / __path__ / __file__
  §5 命名空间包(PEP 420):多个目录拼出一个包,无 __init__.py
  §6 循环导入:为什么 sys.modules 必须"执行前就注册"
  §7 相对导入:点号规则与 __package__
  §8 阻止/替换导入:find_spec 抛异常 vs 返回 None

来源:docs.python.org/3/reference/import.html、/library/importlib.html、PEP 451、PEP 420。
运行:python import_system.py
"""

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import os
import shutil
import sys
import tempfile

SEP = "=" * 72
ROOT = None            # 临时目录,退出时清理


def header(title):
    print(f"\n{SEP}\n{title}\n{SEP}")


def write(relpath, text):
    path = os.path.join(ROOT, relpath)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fp:
        fp.write(text)
    return path


def build_fixture():
    """在临时目录里造一套真实的包/命名空间包/循环导入样本。"""
    global ROOT
    ROOT = tempfile.mkdtemp(prefix="impdemo_")
    write("mypkg/__init__.py", 'print("    [exec] mypkg/__init__.py"); NAME = "mypkg"\n')
    write("mypkg/alpha.py",
          'print("    [exec] mypkg/alpha.py")\n'
          'VALUE = "alpha"\n'
          'PKG = __package__\n'
          'SPEC_NAME = __spec__.name\n'
          'LOADER = type(__loader__).__name__\n')
    write("mypkg/sub/__init__.py", 'print("    [exec] mypkg/sub/__init__.py")\n')
    write("mypkg/sub/beta.py",
          'print("    [exec] mypkg/sub/beta.py")\n'
          'from ..alpha import VALUE          # 相对导入:两个点 -> 上一级包\n'
          'BETA_PKG = __package__\n'
          'BETA_VALUE = VALUE\n')
    write("mypkg/gamma.py", 'print("    [exec] mypkg/gamma.py")\nGAMMA = "gamma"\n')
    write("ns1/nsdemo/one.py", 'NAME = "one"\n')
    write("ns2/nsdemo/two.py", 'NAME = "two"\n')
    write("circ/__init__.py", 'print("    [exec] circ/__init__.py")\n')
    write("circ/a.py",
          'import circ.b\n'
          'print("    [a] 导入 circ.b 之后才定义 A_READY")\n'
          'A_READY = True\n')
    write("circ/b.py",
          'import sys\n'
          'from circ import a                 # 反向导入正在初始化的 circ.a\n'
          'print("    [b] 此刻 circ.a 已在 sys.modules 中:")\n'
          'print("        hasattr(a, \'A_READY\') =", hasattr(a, "A_READY"))\n'
          'B_READY = True\n')
    return ROOT


# ---------------------------------------------------------------------------
# §1 sys.modules 缓存
# ---------------------------------------------------------------------------
def demo_module_cache():
    header("[1] sys.modules:导入的第一站也是缓存")
    sys.path.insert(0, ROOT)
    importlib.invalidate_caches()
    a1 = importlib.import_module("mypkg.alpha")
    a2 = importlib.import_module("mypkg.alpha")
    print(f"  两次 import_module 拿到同一对象:{a1 is a2}")
    print(f"  sys.modules 中的相关键:{[k for k in sys.modules if k.startswith('mypkg')]}")
    print("  import 文档:导入过 foo.bar.baz 后,sys.modules 会同时含 foo、foo.bar、foo.bar.baz")
    reloaded = importlib.reload(a1)
    print(f"  importlib.reload 复用同一模块对象:{reloaded is a1}(会重跑模块代码)")
    saved = sys.modules.pop("mypkg.alpha")       # 只删缓存键,模块对象仍被 a1 引用
    a3 = importlib.import_module("mypkg.alpha")
    print(f"  删掉缓存键后重新导入 -> 新对象:{a3 is not saved}、a3 is a1:{a3 is a1}")
    print("  文档:'the two module objects will not be the same';"
          " 而 importlib.reload 会复用同一对象")
    sys.modules["mypkg.alpha"] = None            # 特殊值 None = 视为"找不到"
    try:
        importlib.import_module("mypkg.alpha")
    except ModuleNotFoundError as exc:
        print(f"  sys.modules[name] = None -> ModuleNotFoundError: {exc}")
    sys.modules["mypkg.alpha"] = saved           # 复原缓存,便于后续章节
    print(f"  复原后 sys.modules['mypkg.alpha'] is a1:{sys.modules['mypkg.alpha'] is a1}")


# ---------------------------------------------------------------------------
# §2 finder / loader / spec
# ---------------------------------------------------------------------------
class LoggingFinder:
    """只记录、不处理:返回 None 表示"我不管",让后面的 finder 接手。"""

    calls = []

    def find_spec(self, fullname, path=None, target=None):
        LoggingFinder.calls.append((fullname, None if path is None else list(path)))
        return None


def demo_finder_chain():
    header("[2] meta path finder:find_spec(fullname, path, target) 的调用序列")
    finder = LoggingFinder()
    sys.meta_path.insert(0, finder)
    importlib.invalidate_caches()
    LoggingFinder.calls.clear()
    importlib.import_module("mypkg.sub.beta")
    names = [("LoggingFinder" if isinstance(f, LoggingFinder)
              else (f.__name__ if isinstance(f, type) else type(f).__name__))
             for f in sys.meta_path]
    print(f"  sys.meta_path 上依次是(默认三个 + 我们的):{names}")
    print("  find_spec 调用序列(name, path):")
    for name, path in LoggingFinder.calls:
        print(f"    {name:<16} path={path}")
    print("  文档:'importing foo.bar.baz will first perform a top level import, calling"
          " mpf.find_spec(\"foo\", None, None)';子模块的 path 参数是**父包的 __path__**")
    print("  注意:这里只看到两级调用,因为 mypkg 在 §1 已经导入过(顶层包那次调用被缓存"
          "短路了);全新顶层包会先打一次 find_spec(name, None, None)")
    print("  另一条:finder 不负责加载,'If they can find the named module, they return a"
          " module spec ... which the import machinery then uses when loading the module'")
    sys.meta_path.remove(finder)


# ---------------------------------------------------------------------------
# §3 注入虚拟模块
# ---------------------------------------------------------------------------
class StringLoader(importlib.abc.Loader):
    """最小的 loader 协议:create_module 返回 None 用默认 ModuleType,exec_module 跑源码。"""

    def __init__(self, source):
        self.source = source

    def exec_module(self, module):
        module.__dict__["SOURCE"] = self.source
        exec(compile(self.source, f"<virtual:{module.__name__}>", "exec"), module.__dict__)


class StringFinder:
    KEY = "virtual_hello"

    def find_spec(self, fullname, path=None, target=None):
        if fullname != StringFinder.KEY:
            return None
        src = 'GREETING = "hello from a file-less module"\n'
        return importlib.machinery.ModuleSpec(fullname, StringLoader(src))


def demo_virtual_module():
    header("[3] 用 ModuleSpec 注入虚拟模块(磁盘上没有对应文件)")
    finder = StringFinder()
    sys.meta_path.insert(0, finder)
    mod = importlib.import_module(StringFinder.KEY)
    print(f"  导入 {StringFinder.KEY} 成功:{mod.GREETING}")
    print(f"  __spec__ = {mod.__spec__}")
    print(f"  __loader__ = {type(mod.__loader__).__name__};hasattr(__file__) = "
          f"{hasattr(mod, '__file__')}")
    sys.meta_path.remove(finder)
    sys.modules.pop(StringFinder.KEY, None)


# ---------------------------------------------------------------------------
# §4 真实包结构
# ---------------------------------------------------------------------------
def demo_package_attrs():
    header("[4] 真实包:模块 vs 包的判定与元数据属性")
    pkg = importlib.import_module("mypkg")
    alpha = importlib.import_module("mypkg.alpha")
    print(f"  mypkg 有 __path__ 吗:{hasattr(pkg, '__path__')} -> 它是包"
          f"({list(pkg.__path__)})")
    print(f"  mypkg.alpha 有 __path__ 吗:{hasattr(alpha, '__path__')} -> 它是模块")
    print("  文档:'By definition, if a module has a __path__ attribute, it is a package'")
    for key, value in (("__name__", alpha.__name__), ("__package__", alpha.PKG),
                       ("__file__", alpha.__file__), ("__spec__.name", alpha.SPEC_NAME),
                       ("__loader__", alpha.LOADER)):
        print(f"  mypkg.alpha.{key} = {value}")
    print(f"  验证 pkg.alpha is getattr(pkg, 'alpha') -> {alpha is getattr(pkg, 'alpha')}"
          "(应为 True;这里是 False,因为 §1 手工改过 sys.modules —— 重新导入产生的对象被设成"
          "pkg.alpha 属性,缓存键又被复原成旧对象)")
    print("  文档不变式:'if you have sys.modules['spam'] and sys.modules['spam.foo']"
          " ..., the latter must appear as the foo attribute of the former' —— 别手改 sys.modules")


# ---------------------------------------------------------------------------
# §5 命名空间包
# ---------------------------------------------------------------------------
def demo_namespace_package():
    header("[5] 命名空间包(PEP 420):多个目录拼出一个没有 __init__.py 的包")
    sys.path.insert(0, os.path.join(ROOT, "ns2"))
    sys.path.insert(0, os.path.join(ROOT, "ns1"))
    importlib.invalidate_caches()
    ns = importlib.import_module("nsdemo")
    print(f"  nsdemo.__path__ 类型 = {type(ns.__path__).__name__};"
          f"portions = {[os.path.basename(os.path.dirname(p)) for p in ns.__path__]}")
    print(f"  nsdemo.__spec__.origin = {ns.__spec__.origin};"
          f"submodule_search_locations = {len(list(ns.__spec__.submodule_search_locations))} 个")
    one = importlib.import_module("nsdemo.one")
    two = importlib.import_module("nsdemo.two")
    print(f"  两个 portion 里的模块都能导入:{one.NAME} / {two.NAME}")
    print("  文档:'With namespace packages, there is no parent/__init__.py file. In fact,"
          " there may be multiple parent directories found during import search';"
          "其 __path__ 是自定义可迭代类型,父路径变化时会重新搜索")


# ---------------------------------------------------------------------------
# §6 循环导入
# ---------------------------------------------------------------------------
def demo_circular():
    header("[6] 循环导入:sys.modules 必须【执行前】注册才不至于死锁")
    print("  import circ.a ->")
    mod = importlib.import_module("circ.a")
    print(f"  完成:circ.a.A_READY = {mod.A_READY}、circ.b.B_READY = "
          f"{sys.modules['circ.b'].B_READY}")
    print("  文档:'The module will exist in sys.modules before the loader executes the"
          " module code. This is crucial because the module code may (directly or indirectly)"
          " import itself'")
    print("  所以 b 里 from circ import a 拿到的是【半成品】模块对象:名字已存在,"
          "但尚未定义完属性 → 顶层语句里访问 a.A_READY 会 AttributeError")
    print("  规避:把交叉引用推迟到函数体内(延迟求值),或把共享部分抽到第三个模块")
    print("  另:导入失败时只有失败的模块会被移出缓存 ——"
          " 'If loading fails, the failing module - and only the failing module - gets removed"
          " from sys.modules'")


# ---------------------------------------------------------------------------
# §7+§8 相对导入与阻止导入
# ---------------------------------------------------------------------------
def demo_relative_and_blocking():
    header("[7] 相对导入与 __package__")
    beta = importlib.import_module("mypkg.sub.beta")
    print(f"  mypkg/sub/beta.py 里 'from ..alpha import VALUE':BETA_PKG = {beta.BETA_PKG}、"
          f"BETA_VALUE = {beta.BETA_VALUE}")
    print("  点号规则:一个点=当前包,每多一个点向上一级;"
          "相对导入只能用 from ... import ... 形式(import .x 不是合法表达式)")

    header("[8] 阻止导入:find_spec 抛异常 vs 返回 None")
    banned_calls = []

    class Blocker:
        def find_spec(self, fullname, path=None, target=None):
            banned_calls.append(fullname)
            if fullname == "banned_module":
                raise ModuleNotFoundError("import of banned_module is blocked by policy")
            return None

    blocker = Blocker()
    sys.meta_path.insert(0, blocker)
    try:
        importlib.import_module("banned_module")
    except ModuleNotFoundError as exc:
        print(f"  抛 ModuleNotFoundError -> 立即终止搜索:{exc}")
    count_before = len(banned_calls)
    gamma = importlib.import_module("mypkg.gamma")     # 全新模块,一定会走 meta_path
    print(f"  返回 None 的模块不受影响:继续走到下一个 finder 并成功导入 "
          f"(gamma.GAMMA={gamma.GAMMA};Blocker 日志新增 "
          f"{len(banned_calls) - count_before} 次调用)")
    print("  文档:'it is sufficient to raise ModuleNotFoundError directly from find_spec()"
          " instead of returning None';返回 None 表示继续搜索,抛异常则立即终止")
    sys.meta_path.remove(blocker)


def main():
    print(f"Python {sys.version}")
    build_fixture()
    try:
        demo_module_cache()
        demo_finder_chain()
        demo_virtual_module()
        demo_package_attrs()
        demo_namespace_package()
        demo_circular()
        demo_relative_and_blocking()
    finally:
        for entry in (ROOT, os.path.join(ROOT, "ns1"), os.path.join(ROOT, "ns2")):
            while entry in sys.path:
                sys.path.remove(entry)
        shutil.rmtree(ROOT, ignore_errors=True)
        print(f"\n临时目录已清理:{ROOT}")
    print("全部章节执行完毕。")


if __name__ == "__main__":
    main()
