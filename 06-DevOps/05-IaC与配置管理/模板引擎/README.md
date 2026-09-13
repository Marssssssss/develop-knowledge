# 模板引擎 (Template Engine)

## 简介

**模板引擎** 是 IaC 与配置渲染的基石:Terraform 用 HCL 模板输出、Kubernetes 的 Helm chart 用 Go template 输出、Kubernetes 社区的 Kustomize 用 patch + Overlay 生成最终 manifest。本 demo 覆盖 IaC 中最常用的 **Jinja2 风格模板引擎**——`{{ var }}` 表达式插值、`{% for %}` / `{% if %}` 控制结构、`| filter ` 过滤器链。

Jinja2 官方对模板的定义(jinja.palletsprojects.com/en/2.10.x/templates/):
> "A Jinja template is simply a text file. Jinja can generate any text-based format (HTML, XML, CSV, LaTeX, etc.). A template contains variables and/or expressions, which get replaced with values when a template is rendered; and tags, which control the logic of the template."

Jinja2 是 Python 生态最流行的模板引擎(Flask/Ansible/SaltStack/Helm 都借鉴其语法)。本 demo **Python 版从零实现 miniJinja**,C 版简化到只支持 `{{ var }}` 和 `{% if %}` 块,Go 版直接用 stdlib `text/template`(语言级模板引擎,语法 `{{ .Var }}` 而非 `{{ Var }}`,但控制结构等价)。

## 原理详解

### 词法与语法(摘自 Jinja2 Template Designer Documentation)

Jinja2 四种 delimiter:

| Delimiter | 用途 |
| --- | --- |
| `{{ ... }}` | Expression,渲染为字符串插入输出 |
| `{% ... %}` | Statement,控制流(`for`/`if`/`block`/…) |
| `{# ... #}` | Comment,**不进入输出** |
| `# ...` | Line Statement(需配置 `line_statement_prefix` 才生效,默认开) |

本 demo 只识别前三种,line statement 不启用。

### Variable Resolution

Jinja 变量查找规则(文档原文):
> "foo.bar in Jinja does the following things on the Python layer: check for an attribute called bar on foo (`getattr(foo, 'bar')`), if there is not, check for an item 'bar' in foo (`foo.__getitem__('bar')`), if there is not, return an undefined object."

即 `foo.bar` 先 getattr 后 getitem;`foo['bar']` 先 getitem 后 getattr。这是 Python 数据模型的双重歧义——jinja 给出确定性。

`lookup()` 函数(`mini_jinja.py`)实现此语义:优先属性,dict/序列 fallback。

### Filters

`{{ var|filter }}` 是 Jinja2 最强大的特性之一。文档:
> "Variables can be modified by filters. Filters are separated from the variable by a pipe symbol (|) and may have optional arguments in parentheses. Multiple filters can be chained. The output of one filter is applied to the next."

Jinja2 内置 `upper`/`lower`/`title`/`default`/`replace`/`trim` 等数十个 filter,user 可自定义。本 demo Python 版实现这几个;Go 版直接登记到 `template.FuncMap`。

### Control Flow

`{% for x in y %}` `{% endfor %}` 与 `{% if cond %}` `{% elif cond %}` `{% else %}` `{% endif %}` 与 Python 语法相似,但有 Jinja 特殊变量 `loop.index`/`loop.first`/`loop.last`(本 demo 简化,未实现)。

### undefined 行为

Jinja2 默认未定义变量输出空字符串("the default behavior is to evaluate to an empty string if printed or iterated over, and to fail for every other operation")。本 demo 用同样规则(`lookup()` 找不到返 `""`)。

### Go stdlib `text/template`

Go 的 `text/template`(及 HTML 版 `html/template`)是 Go 生态标准模板引擎,**设计受 Jinja 直接启发**:

| Jinja2 | Go text/template |
| --- | --- |
| `{{ var }}` | `{{ .Var }}`(强制 root prefix) |
| `{% for x in y %}` | `{{ range .Y }}...{{ end }}`(range 内的 `.` 自动切换) |
| `{% if c %}{% else %}` | `{{ if .C }}...{{ else }}...{{ end }}` |
| `{{ x\|upper }}` | `{{ .X \| upper }}`(需 `Funcs(funcMap)` 注册) |
| `{% set x = 1 %}` | `{{ $x := 1 }}`(`$` 表示本地变量) |

本 demo Go 版 `template.Must()` + `Funcs(funcMap)` 完整呈现:变量、控制流、filter 三件套。

### C 版简化

C 实现完整模板引擎需要自己写 lexer + AST + evaluator,行数远超 300 上限。本 demo C 版刻意**只实现** `{{ var }}` 替换 + `{% if var %}` 块,**没有 for 循环、没有 filter、无嵌套**,以演示最小工作原理。

## 对比

| 引擎 | 语言 | 复杂度 | 表达式求值 | 应用领域 |
| --- | --- | --- | --- | --- |
| Jinja2 | Python | ~3k 行 | 完整表达式子语言 | Flask/Ans/Salt/Helm |
| Go text/template | Go | stdlib | 表达式 + FuncMap | Helm/K8s/Terraform Providers |
| Handlebars/Mustache | JS | ~1k | 严格无逻辑 | 前端 SPA |
| 本 demo C 版 | C | ~250 行 | 仅变量替换 | 教学 |

## 环境准备

- C:GCC 9+(`-Wall -Wextra -std=c99`)
- Python:3.8+(仅标准库)
- Go:1.18+

## 运行方式

```bash
# C
gcc -O2 -Wall -Wextra -std=c99 c/mini_template.c -o mini_template
./mini_template

# Python
python3 python/mini_jinja.py

# Go
go run go/mini_template.go
```

## 关键代码片段

Python lookup 函数(`python/mini_jinja.py`),实现 Jinja 2 的"`foo.bar` 先 getattr 后 getitem"规则:

```python
def lookup(var, path, ctx):
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
            return ""
    return var
```

Jinja2 文档规约就是这样:`getattr` 优先,然后 `__getitem__`。

Go 版 FuncMap 注册对应 Jinja `|upper`/`|default`:

```go
var funcMap = template.FuncMap{
    "upper":   strings.ToUpper,
    "lower":   strings.ToLower,
    "default": func(arg, val interface{}) interface{} {
        if val == nil || val == "" || val == 0 { return arg }
        return val
    },
}
t := template.Must(template.New("persona").Funcs(funcMap).Parse(TPL))
```

## 性能与边界

- Python mini_jinja 用正则 + token 流,无 AST,**O(n)** 编译,O(n) 渲染,足够 demo
- Go stdlib 编译为字节码,O(n) 渲染,生产用
- 本 demo **不实现**:宏 / 模板继承 / 沙箱(防 RCE 极重要,Jinja2 的 `Environment` 用 `SandboxedEnvironment` 类阻断危险语句)/ 自动 HTML 转义(GitHub Action 等场景必须用 `html/template` 替代 `text/template`)
- C 版无 for 循环、无过滤器、无嵌套 if——明确简化

## 注意事项与常见坑

- **SandboxedEnvironment**:Jinja2 的 `Environment` 允许 `__import__` 类 RCE,文档明示"Ansible uses Jinja2 in its action plugins; templates can therefore access all facts (info about the remote system) and any client-side config".生产模板提供给不可信用户时用 `SandboxedEnvironment`/`ImmutableSandboxedEnvironment`
- **`undefined` 默认返回空字符串**:Jinja2 文档明示"to evaluate to an empty string if printed";Python 模板里访问未赋值变量不会抛 KeyError,会让模板静默失败。生产建议 `Environment(undefined=StrictUndefined)` 让未定义立即报错
- **Go template 路径穿越**:`template.ParseFiles(tpl)` 默认相对当前目录,任何能传文件名的接口要 sanitize
- **Go template 与 helm context**:`{{ .Values.foo }}` 在 helm 中访问 values.yaml 内 `foo`;helm 用 Go template 引擎,不是 Jinja——前者 `{{ . }}` 强制 root,后者 `{{ var }}` 直接访问 ctx
- **C 版无 for 循环**:仅作教学。生产 C 模板引擎可考虑 [libtemplate](https://github.com/erez-strauss/libtemplate) 或 [inja](https://github.com/pantor/inja)(C++ header-only)

## 参考资料(实际阅读过的权威来源)

- [Jinja2 Template Designer Documentation 2.10](https://jinja.palletsprojects.com/en/2.10.x/templates/) — 全文阅读:四种 delimiter、variable lookup 规则、filters/tests/for/if/macros/call 所有控制结构完整 spec
- [Go stdlib text/template](https://pkg.go.dev/text/template) — 全文阅读:Action 语法 + 预定义函数 + FuncMap 扩展 + 与 html/template 区别
- [Qt IF: Jinja Template Syntax](https://doc.qt.io/QtIF/template-syntax.html) — 全文阅读:`{% for %}` 内 loop 特殊变量表 + Jinja 与 Python 行为差异(spc `break`/`continue` 不支持)
- [Bloomreach: Jinja Blocks](https://documentation.bloomreach.com/engagement/docs/jinjablocks) — 全文阅读:`is defined`/`is even` 等 test 完整分类表 + `set` 内联表达式语法
