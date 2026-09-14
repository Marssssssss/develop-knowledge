# Python 导入机制(import machinery,PEP 451)

## 简介

`import` 不是"读文件",而是一条**协议流水线**:先查缓存,再让一串 **finder** 决定"谁能找到它",
finder 交出 **spec**(模块规格),最后由 **loader** 创建模块对象并执行其代码。

- 一句话:`sys.modules` 是缓存 → `sys.meta_path` 找 spec → loader 造模块 + 跑代码。
- 关键概念:
  - **module cache(`sys.modules`)**:所有已导入模块的字典,导入的第一站
  - **finder / importer**:能定位模块的对象,实现 `find_spec(name, path, target)`
  - **ModuleSpec**:把"怎么加载"从 finder 传给导入系统的数据包(含 loader、origin、`submodule_search_locations`)
  - **loader**:实现 `create_module(spec)` 与 `exec_module(module)`
  - **命名空间包(PEP 420)**:没有 `__init__.py`、由多个目录"拼"出来的包
- 历史:3.3 完整实现 PEP 302 第二阶段(无隐式导入机制,一切走 `sys.meta_path`)+ PEP 420 命名空间包;
  3.4 引入 module spec(PEP 451)取代"finder 直接返回 loader";3.12 移除废弃的 `find_module()`。

## 原理详解

### 1. 触发方式与 `import` 语句的两件事

三种触发路径:`import` 语句、`importlib.import_module()`、内置 `__import__()`。文档区分得很清楚:

> "The `import` statement combines two operations; it searches for the named module, then it
> binds the results of that search to a name in the local scope."
> "A direct call to `__import__()` performs only the module search and, if found, the module
> creation operation."

### 2. 加载流程(官方文档的伪代码,简化后)

```text
import foo.bar.baz
  └─ sys.modules 查 "foo.bar.baz":命中且非 None -> 直接用;值为 None -> ModuleNotFoundError
       └─ 未命中 -> 依次调用 sys.meta_path 中每个 finder 的 find_spec(name, path, target)
            ├─ 全部返回 None -> ModuleNotFoundError
            └─ 返回 ModuleSpec -> create_module(spec) -> _init_module_attrs(...)
                 -> sys.modules[spec.name] = module(执行代码之前就注册!)
                 -> exec_module(module)(跑代码;失败则只把该模块移出缓存)
```

文档中四条关于加载的关键说明:

1. "The module will exist in `sys.modules` **before** the loader executes the module code.
   This is crucial because the module code may (directly or indirectly) import itself..."
   —— 这就是循环导入不会死锁的原因。
2. "If loading fails, the failing module – and only the failing module – gets removed from
   `sys.modules`."
3. "After the module is created but before execution, the import machinery sets the
   import-related module attributes."
4. "The module created during loading and passed to `exec_module()` may not be the one
   returned at the end of import."

### 3. meta path 与 path based finder

- `sys.meta_path` 默认三项(实测 `['BuiltinImporter', 'FrozenImporter', 'PathFinder']`):
  内置模块、冻结模块、基于路径的 finder;`find_spec()` 的三参数是模块全限定名、搜索路径、目标模块(仅 reload 传入)。
  子模块的 path 参数就是**父包的 `__path__`**:
  "importing `foo.bar.baz` will first perform a top level import, calling
  `mpf.find_spec("foo", None, None)`... After `foo` has been imported, `foo.bar` will be
  imported by traversing the meta path a second time, calling
  `mpf.find_spec("foo.bar", foo.__path__, None)`."
- **finder 不加载模块**:"Finders do not actually load modules. If they can find the named
  module, they return a *module spec* ... which the import machinery then uses when loading."
- 基于路径的 finder 用三个变量:`sys.path`、`sys.path_hooks`、`sys.path_importer_cache`
  (缓存路径 → path entry finder,因为"this can be an expensive operation");空字符串项
  (当前工作目录)每次查找都重新求值,不长期缓存。

### 4. ModuleSpec

"The purpose of a module's spec is to encapsulate this import-related information on a
per-module basis." 暴露为 `module.__spec__`;`__main__` 在交互式/`-c`/stdin/直接跑脚本时其
`__spec__` 为 `None`(只有 `-m` 运行才会被设上)。

### 5. 模块属性速查(本 demo 实测值)

| 属性 | 含义 | 实测(`mypkg/alpha.py`) |
| --- | --- | --- |
| `__name__` | 全限定名 | `mypkg.alpha` |
| `__package__` | 所在包(用于相对导入) | `mypkg` |
| `__file__` | 源文件路径 | `.../mypkg/alpha.py` |
| `__spec__.name` | spec 里的模块名 | `mypkg.alpha` |
| `__loader__` | 负责加载它的 loader | `SourceFileLoader` |
| `__path__` | **只有包才有** | 普通模块没有该属性 |

判定包的唯一标准:"**By definition, if a module has a `__path__` attribute, it is a package.**"

### 6. 常规包 vs 命名空间包

| | 常规包 | 命名空间包(PEP 420) |
| --- | --- | --- |
| 标志 | 目录含 `__init__.py` | **没有** `__init__.py` |
| `__path__` 类型 | 普通 list | 自定义可迭代类型(`_NamespacePath`) |
| 来源 | 单个目录 | 多个 **portion**,可分布在不同目录/zip/网络 |
| `__spec__.origin` / 用途 | 文件路径;常规库 | `None` 且 `submodule_search_locations` 非空;把同名子包拆给不同发行方 |

文档:"With namespace packages, there is no `parent/__init__.py` file. In fact, there may be
multiple `parent` directories found during import search";其 `__path__` 会在父路径变化时
**重新搜索**。判据(伪代码):`spec.origin is None and spec.submodule_search_locations is not None`。

### 7. 相对导入

- 一个点 = 当前包,每多一个点向上走一级:`from . import x` / `from ..pkg import y`。
- 只能用 `from ... import ...` 形式:文档解释 "`import XXX.YYY.ZZZ` should expose
  `XXX.YYY.ZZZ` as a usable expression, but `.moduleY` is not a valid expression."
- 依赖 `__package__`;脚本被直接执行时 `__package__` 为空 → 相对导入报
  "attempted relative import with no known parent package"。

### 8. 缓存字节码(`.pyc`)

加载前会校验缓存是否最新:默认把源码的 **mtime + size** 写进 `.pyc`;3.7 起还支持
**基于哈希**的变体(checked / unchecked),可用 `--check-hash-based-pycs` 调整校验行为。

## 对比 / 选型

| 方式 | 语义 | 适用场景 |
| --- | --- | --- |
| `import x` / `from x import y` | 搜索 **+ 名字绑定** | 静态代码 |
| `importlib.import_module("x.y")` | 只搜索/创建模块并返回,**不做名字绑定** | 按字符串动态导入、插件加载 |
| `__import__("x.y")` | 同上但 API 更原始(默认返回顶层包) | 底层/兼容旧代码 |
| `importlib.reload(m)` | 复用**同一**模块对象重跑代码(不重建) | 开发期热重载 |
| 手改 `sys.modules` | 直接干预缓存 | 只用于测试桩;**会破坏文档不变式** |

## 环境准备

- 操作系统:任意;语言版本 Python 3.4+(module spec,实测 3.13.14);依赖仅标准库(演示在系统临时目录建样本包并清理)

## 运行方式

```bash
python import_system.py
```

## 关键代码片段

一个"只记录不处理"的 finder —— 返回 `None` 表示"我不管",让后面的 finder 接手:

```python
class LoggingFinder:
    calls = []
    def find_spec(self, fullname, path=None, target=None):
        LoggingFinder.calls.append((fullname, None if path is None else list(path)))
        return None            # 关键:返回 None 才会继续搜索

sys.meta_path.insert(0, LoggingFinder())
importlib.import_module("mypkg.sub.beta")
# calls == [('mypkg.sub', [<mypkg 目录>]), ('mypkg.sub.beta', [<.../mypkg/sub>])]
```

注入一个磁盘上不存在的模块(spec + loader 的最小实现):

```python
class StringLoader(importlib.abc.Loader):
    def __init__(self, source): self.source = source
    def exec_module(self, module):               # create_module 不实现 -> 用默认 ModuleType
        exec(compile(self.source, "<virtual>", "exec"), module.__dict__)

class StringFinder:
    def find_spec(self, fullname, path=None, target=None):
        if fullname != "virtual_hello":
            return None
        return importlib.machinery.ModuleSpec(fullname, StringLoader('X = 1'))
```

## 性能与边界

- `sys.modules` 命中是 O(1) 字典查找,导入的"热路径"基本零成本。
- 未命中时逐项遍历 `sys.meta_path`,再交给 path based finder 查 `sys.path_importer_cache`
  (路径 → path entry finder),最后才落到文件系统。
- 文件系统层面的目录列表会被缓存,所以**运行时新增目录/文件后**要
  `importlib.invalidate_caches()`(本 demo 每次改 `sys.path` 后都调用了它)。
- 边界:`sys.path` 里"只应有字符串,其他类型一律忽略";`.pyc` 校验依赖 mtime+size(或哈希),
  时间戳回退可能导致误用旧缓存。

## 注意事项与常见坑

1. **不要手改 `sys.modules`**。删除键只是让缓存失效(模块对象可能仍被别处引用,下次导入会得到
   **另一个**对象);赋 `None` 会让之后的导入直接 `ModuleNotFoundError`。本 demo §4 的
   `pkg.alpha is getattr(pkg, 'alpha') == False` 就是手工破坏不变式的现场:
   文档要求 "if you have `sys.modules['spam']` and `sys.modules['spam.foo']`... the latter must
   appear as the `foo` attribute of the former"。
2. **循环导入拿到的是半成品模块**:模块在执行前就进缓存,所以反向导入能看到"名字存在但属性没定义"
   的模块 —— 顶层语句里访问未定义的属性会 `AttributeError`。把交叉引用推迟到函数里,或抽出第三个模块。
3. **`reload` 不重建对象**:`importlib.reload` 复用同一模块对象并重跑代码;而"删缓存键再导入"
   会得到新对象,两者语义完全不同,别混用。
4. **相对导入依赖 `__package__`**:直接用 `python sub/mod.py` 运行会失败(没有已知父包),
   应 `python -m pkg.sub.mod` 或改用绝对导入。
5. **`find_spec` 返回 `None` 与抛异常含义相反**:返回 `None` = 继续搜索;直接抛
   `ModuleNotFoundError` = 立即终止整个搜索(文档推荐的"策略阻止导入"手段)。
6. **`load_module()` / `find_module()` 已移除**(3.10 起弃用警告,3.12 移除);
   自定义 loader 请实现 `create_module` + `exec_module`。
7. **包和模块别混为一谈**:`__path__` 存在即是包;给普通模块手写 `__path__` 不会让它变成包。

## 参考资料(实际阅读过的权威来源)

- [The import system(Python 语言参考第 5 章)](https://docs.python.org/3/reference/import.html) — 完整流程伪代码、`sys.modules`/`sys.meta_path`/`sys.path_hooks` 语义、spec 与 loader 协议、模块属性、常规包与命名空间包、相对导入、`.pyc` 校验、`__main__` 特例
- [PEP 451 – A ModuleSpec Type for the Import System](https://peps.python.org/pep-0451/) — finder 返回 spec(而非 loader)的设计与好处、`create_module`/`exec_module` 协议来源
- [PEP 420 – Implicit Namespace Packages](https://peps.python.org/pep-0420/) — 命名空间包的规范(portions、无 `__init__.py`、`_NamespacePath`)
- [importlib — The implementation of import](https://docs.python.org/3/library/importlib.html) — `import_module`/`reload`/`invalidate_caches` 及 `importlib.abc`/`machinery` API
- [PEP 302 – New Import Hooks](https://peps.python.org/pep-0302/) — `sys.meta_path` 与 finder 协议的由来(3.3 完整落地)
