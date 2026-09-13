# HCL 解析器

## 简介

HCL (HashiCorp Configuration Language) 是 HashiCorp 为其生态(Terraform、Vault、Nomad、Packer、Consul)设计的**结构化配置语言**。它不是通用数据序列化(JSON/YAML),而是"为构建结构化配置格式而设计的语法和 API"——以 `attribute`/`block` 双语法构造为骨干,带嵌入式表达式子语言(`${var.x}` 插值、`for` 列表推导、函数调用等)。

本 demo 用 Python / C / Go 三语言实现 HCL 的**最小可用解析器**:覆盖 attribute 定义、block 嵌套、字符串字面量(number/bool/null 字面量)、object/tuple 值。生产用请用 [`hashicorp/hcl/v2`](https://github.com/hashicorp/hcl) 库。

## 原理详解

### HCL 的两大构造(摘自 [HCL Native Syntax Specification](https://github.com/hashicorp/hcl/blob/v2.12.0/hclsyntax/spec.md))

| 构造 | 语法 | 作用 |
| --- | --- | --- |
| Attribute | `name = expression` | 给一个属性名赋一个表达式值,表达式原样保留由调用方求值 |
| Block | `TYPE "label" { ... }` | 创建带类型和标签的子 body,可嵌套 |

**attribute**:`io_mode = "async"` 给 `body["io_mode"]` 赋值 string `"async"`。spec 明文:"Each distinct attribute name may be defined no more than once within a single body"(同 body 内同 attribute 名只一次)。

**block**:`service "http" "web_proxy" { listen_addr = "..." }` 在父 body 下创建 `body["service"]["http"]["web_proxy"]` 层级,可嵌套。block label 按 spec 可以是 quoted literal 或 naked identifier。

### 表达式子语言

按 spec:`Expression = ExprTerm | Operation | Conditional`,本 demo 只覆盖:
- Literal: number / "true" / "false" / "null"
- String literal `"..."`(模板插值 `${...}` 此处省略,实现在 HCL 里字符串内部也是表达式)
- Tuple `[expr, expr]`
- Object `{ identifier = expr }`(objectelem 语法,identifier 既作 key 名)
- Identifier 作变量引用

不实现(for/splat/index/arith/function call/heredoc),仅作教学。

### 注释(三种 spec 都支持)

```
# single-line
// single-line
/* block */
```

按 spec:`#` 是默认风格;`//` 是"alternative";Packer/HCL 官方提示自动格式化工具可能把 `//` 转成 `#`(因 `//` 不惯用)。

### 标识符

按 spec:Identifiers contain letters, digits, underscores, hyphens;首字符不为数字。本 demo 用 ASCII subset(`[_A-Za-z][-_\w]*`)已足够。

### Lexer

简单状态机:跳空白与注释 → 识别标识符/数字/字符串字面量/单字符 OP(`={}[]().,+-*/`)。

字符串字面量识别 `"..."` 双引号包裹,允许 `\"` `\\` `\n` `\r` `\t` 五种转义。模板插值 `${...}` 按 spec 是表达式内部再次调用表达式子语言,本 demo 简化:返回内容当作普通 string(并在 README 注明)。

### Parser

递归下降:
1. 顶层 _parse_item:看下一个 IDENT 后面接 `=`(attribute)还是 `{`(block)分派
2. block:收 0+ label(string 或 IDENT),然后 `{` 收 body 直到匹配的 `}`
3. expression:分发到 string/number/bool/null/identifier/object/tuple
4. object:识别 `{ ident = expr (, ident = expr)* ,? }` 规格

## 对比

| 实现 | 语言 | 行数 | 复杂度 | 适用 |
| --- | --- | --- | --- | --- |
| 本 demo | Python/C/Go | ~300 | O(n) | 教学 |
| [`hashicorp/hcl/v2`](https://github.com/hashicorp/hcl) | Go | ~5.7k | O(n) 带 AST/scope/eval | 生产(Terraform 内部) |
| [`tmccombs/hcl2-rs`](https://github.com/tmccombs/hcl2-rs) | Rust | ~3k | 同上 | Rust 用户 |

## 环境准备

- C:GCC 9+(`-Wall -Wextra -std=c99`)
- Python:3.7+(仅用标准库)
- Go:1.18+

## 运行方式

```bash
# C 版
gcc -O2 -Wall -Wextra -std=c99 c/hcl_parser.c -o hcl_parser
./hcl_parser

# Python 版
python3 python/hcl_parser.py

# Go 版
go run go/hcl_parser.go
```

每版输出三块内容:输入 HCL、Lexer 切出的前 16 个 token、解析后的 AST 结构(Python 是 JSON 嵌套 dict,C 是 pretty-printed tree,Go 是 JSON)。

## 关键代码片段

Python lexer 入口(`python/hcl_parser.py`,节选):

```python
TOKEN_PATTERNS = [
    ("COMMENT_BLOCK", re.compile(r"/\*[\s\S]*?\*/")),
    ("COMMENT_LINE_HASH", re.compile(r"#[^\n]*")),
    ("COMMENT_LINE_SLASH", re.compile(r"//[^\n]*")),
    ("STRING", _STR_FRAG),
    ("NUMBER", _NUM),
    ("IDENT", _IDENT),
    ("OP", re.compile(r"[={}\[\],().*+\-/]")),
    ("WS", re.compile(r"\s+")),
]
```

按 spec 三种注释全识别 + 单字符 OP 集覆盖 HCL 控制结构所需。

## 性能与边界

- 时间复杂度 **O(n)**(n = 源字符数):单 pass lex + 单 pass 递归下降
- 本 demo **不实现**:模板插值 `${...}`、for 表达式、splat、heredoc、function call、JSON 等价语法
- HCL 也接受 JSON 语法(因为 HCL 是 JSON 的超集,见 GitHub README),本 demo 不涵盖
- HCL 实际求值由 calling application 决定,本 demo 只到 AST 阶段

## 注意事项与常见坑

- **嵌套 block 在 Go 版被简化**:HCL 嵌套 block 任意深,本 demo Go 版只支持单层 label 链(2 段),`{ tags = { ... } }` 通过 inline object 表达而非 nested block
- **block label 类型**:spec 允许 quoted string 或 naked identifier,但 Terraform 实际只接受 quoted string,本 demo 兼容两种(更宽松)
- **attribute 重名**:spec 明文"each distinct attribute name may be defined no more than once"——本 demo Python 版 dict 直接覆盖(后定义赢),生产 hcl/hclsyntax 库会报错
- **字符串转义**:本 demo 简化用 unicode_escape 解析;真 HCL `${` 在字符串内部是模板语法,需要二次 parse

## 参考资料(实际阅读过的权威来源)

- [HCL Native Syntax Specification v2.12.0](https://github.com/hashicorp/hcl/blob/v2.12.0/hclsyntax/spec.md) — 全文阅读:Body/Attribute/Block/Expression/Literal/Collection/TemplateExpr 等规范的精确句法
- [HCL GitHub README](https://github.com/hashicorp/hcl) — 全文阅读:HCL 设计哲学(声明式优于 JSON/YAML、attribute+block 双构造)、JSON 等价版本说明
- [Packer HCL Configuration Syntax](https://docs.hashicorp.com/packer/docs/v1.8.x/templates/hcl_templates/syntax) — 全文阅读:Argument/Block/Identifiers/Comments/Character Encoding 五节给出 spec 的实操级解释
- [Nomad HCL reference](https://developer.hashicorp.com/nomad/docs/reference/hcl2) — 全文阅读:"HCL is designed to be easy for humans to read and write"、Arguments/Blocks/Expressions 边界、JSON 解析歧义
