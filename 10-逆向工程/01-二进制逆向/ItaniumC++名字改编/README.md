# 431 Itanium C++ ABI 名字改编（Mangling）

> C++ 之所以支持重载、命名空间、模板，全靠把「名字 + 类型」一起编码进符号里。GCC / Clang 在 ELF 平台上用的这套编码就是 **Itanium C++ ABI §5.1**。`_Z3foov` 不是乱码，它是一个**有文法的、可解析的**字符串。

## 1. 简介

本 demo 是一个**最小解 mangler**（demangler），逐条对着 Itanium C++ ABI §5.1「External Names (a.k.a. Mangling)」实现：

- `_Z` 前缀与 `<encoding>` 的三种形态（函数 / 数据 / special-name）；
- `<nested-name>` 的 `N...E` 包裹与 CV/ref 限定符前缀；
- 21 种内建类型码、42 个操作符码；
- **substitution（替换/压缩）**机制：`S_` / `S<seq-id>_` 与 7 个 `Sx` 固定缩写；
- 指针 / 引用 / 右值引用 / 成员指针 / 数组 / 函数类型 / 模板实参。

刻意**不含**：lambda（`<closure-prefix>`）、表达式模板实参、虚函数表族 `<special-name>`（TV/TT/TI/TS）、`<local-name>` / `<unnamed-type-name>` / `<ctor-dtor-name>`——这些需要编译器上下文才能还原。

## 2. 原理详解（全部出自 §5.1 原文文法）

### 2.1 总体结构

```
<mangled-name> ::= _Z <encoding>
               ::= _Z <encoding> . <vendor-specific suffix>
<encoding>     ::= <function name> <bare-function-type>
               ::= <data name>
               ::= <special-name>
```

> "Entities with C linkage and global namespace variables are not mangled."

所以 `printf` 原样返回，`_Z...` 只出现在 C++ 实体上。含 `$` 或 `.` 的修饰名**保留给实现私有使用**，不可移植（本 demo 对非 `_Z` 前缀一律原样返回）。

顶层函数类型**不需要**嵌套时的分隔符（内嵌的 `<function-type>` 要用 `F...E` 包起来）。文档在示例里用 `Ret?` 表示"mangling 里没给出的返回类型"——**非模板函数的返回类型不进符号**，这正是 C++ 不允许仅按返回类型重载的 ABI 侧体现。

### 2.2 `<number>` 与 `<seq-id>`

```
<number>  ::= [n] <non-negative decimal integer>     # 负数前导 'n'
<seq-id>  ::= <0-9A-Z>+                              # 36 进制
```

`<number>` 用来给标识符加**字节长度前缀**（`3foo`），且**除 0 本身外不带前导零**。

`<seq-id>` 是 base-36，关键在于它的**起点**：

> "wherever `<seq-id>` appears, the first element is encoded by the absence of a number, and the remainder of the sequence is encoded starting at 0."

文档给的原话例子：

> "substitutions are mangled as `S [<seq-id>] _`. The first substitutable entity is encoded as `S_`, i.e. with no number. The second is encoded as `S0_`, the third as `S1_`, the twelfth as `SA_`, the thirty-eighth as `S10_`."

**第 1 个是 `S_`、第 2 个才是 `S0_`** —— 这是最容易写错的一处（本 demo 的断言专门钉住它）。

### 2.3 `<nested-name>` 与限定符

```
<nested-name>   ::= N [<CV-qualifiers>] [<ref-qualifier>] <prefix> <unqualified-name> E
                 ::= N [<CV-qualifiers>] [<ref-qualifier>] <template-prefix> <template-args> E
<CV-qualifiers> ::= [r] [V] [K]        # restrict, volatile, const
<ref-qualifier> ::= R                  # &
                 ::= O                 # &&
```

注意两点：

1. `<prefix>` 递归地把外层作用域一路拆到全局作用域；**`<template-prefix>` 指的是模板名（不带实参）**，名字容易误导。
2. 当 `<nested-name>` 指向**非静态成员函数**时，CV/ref 限定符要**前缀**到复合名上；即使该函数是某个已被替换模板的特化、限定符本可以从替换目标推出来，**这个前缀也是必需的**。

`[r][V][K]` 是 mangling 里的书写顺序（restrict → volatile → const），打印时要反过来：`const` 最靠近基类型。

### 2.4 类型前缀与内建类型

```
<type> ::= P <type>   # pointer
       ::= R <type>   # l-value reference
       ::= O <type>   # r-value reference (C++11)
       ::= C <type>   # complex pair (C99)
       ::= G <type>   # imaginary (C99)
       ::= <substitution>
```

内建类型 21 个单字母码（本 demo 全覆盖）：

| 码 | 类型 | 码 | 类型 | 码 | 类型 |
| --- | --- | --- | --- | --- | --- |
| `v` | void | `i` | int | `f` | float |
| `w` | wchar_t | `j` | unsigned int | `d` | double |
| `b` | bool | `l` | long | `e` | long double |
| `c` | char | `m` | unsigned long | `g` | __float128 |
| `a` | signed char | `x` | long long | `z` | ... |
| `h` | unsigned char | `y` | unsigned long long | `n`/`o` | __int128 / unsigned |
| `s` | short | `t` | unsigned short | | |

### 2.5 操作符编码

> "Unlike Cfront, unary and binary operators using the same symbol have different encodings. Most operators are encoded using exactly two letters, the first of which is lowercase."

所以 `+` 有 `pl`（二元）和 `ps`（一元）两个码，`-` 有 `mi`/`ng`，`&` 有 `an`/`ad`，`*` 有 `ml`/`de`——**本 demo 的表里恰好有 4 个符号在值上重复**，这条断言就是验它。

厂商扩展操作符：`v <digit> <source-name>`，即 `v` + 操作数个数（一位十进制）+ 长度前缀名。

### 2.6 Substitution：mangling 里最绕的部分

规则原文要点：

- 组件**从左到右**考虑，**先于**包含它的复合结构入表；
- 见过就替换，没见过就编码并加入候选字典；**同一个实体不会入表两次**；
- 可替换的是**所表示的符号构造**，不是它的字符串：所以"替换过的对象与未替换形式匹配"、"带分隔符的 `<function-type>` 与 `<bare-function-type>` 匹配"；
- **非静态成员函数的类型，与看起来相同的命名空间作用域 / 静态成员函数类型，视为不同**；两个不同类的非静态成员函数类型也视为不同（函数所属的类算作类型的一部分）。

文档给的例子：

```c++
typedef void T();
struct S {};
void f(T*, T (S::*)) {}
// mangled as _Z1fPFvvEM1SFvvE
```

第二个参数（成员函数指针）的类型**不**与第一个（普通函数指针）共享替换。

7 个固定缩写（不占序列号）：

| 缩写 | 展开 |
| --- | --- |
| `St` | `::std::` |
| `Sa` | `::std::allocator` |
| `Sb` | `::std::basic_string` |
| `Ss` | `::std::basic_string<char, ::std::char_traits<char>, ::std::allocator<char> >` |
| `Si` | `::std::basic_istream<char, std::char_traits<char> >` |
| `So` | `::std::basic_ostream<char, std::char_traits<char> >` |
| `Sd` | `::std::basic_iostream<char, std::char_traits<char> >` |

> "Note that the abbreviation `St` does not require `N...E` delimiters unless either followed by more than one additional composite name component, or preceded by CV-qualifiers or a ref-qualifier for a member function."

例：`_ZSt5state` = `::std::state`；`_ZNSt3_In4wardE` = `::std::_In::ward`（这里因为后面跟了**两个**组件 `_In` 和 `ward`，所以需要 `N...E`）。

### 2.7 模板实参

类型实参用常规编码。文档原文例子：

- `A<char, float>` → `1AIcfE`
- 依赖类型 `A<T2>::X`（T2 是第 2 个模板参数）→ `N1AIT0_E1XE`（`T0_` 表示第 2 个参数，从 0 开始数）
- `A g(A) { }` → `_Z1gN3Foo1AE`

## 3. ABI tag 的一个副作用（顺带记）

`[[gnu::abi_tag("X")]]` 会追加 `B1X`；因为 `std::string` 走 `Ss` 缩写、第二个参数又替换成 `S_`，于是：

```c++
void f(std::string, std::string) { }  // mangles as _Z1fSsB1XS_
```

## 4. 环境与运行方式

```bash
cd 10-逆向工程/01-二进制逆向/ItaniumC++名字改编
python mangling_check.py     # 40 条断言，全部实跑通过
go run mangling.go           # 需 Go 工具链（本机无，走人工审查 + 机械核查）
```

无依赖，纯标准库。

## 5. 关键代码

```python
BUILTIN = {"v": "void", "i": "int", "z": "...", ...}      # 21 种
OPERATORS = {"pl": "+", "ps": "+", "mi": "-", "ng": "-", ...}  # 42 条，4 个值重复
SUBST_ABBREV = {"St": "std", "Ss": "std::basic_string<char, ...>", ...}

def _sub(self, tok):          # S_ ⇒ 第 1 个；S0_ ⇒ 第 2 个；base-36 解码
    if tok == "S_":
        return self.subs[0]
    return self.subs[int(tok[1:-1], 36) + 1]
```

## 6. 性能边界

- 这是**教学用最小实现**，不是 `c++filt` 的替代品：不处理 lambda、`<local-name>`、`<special-name>` 的虚表族、表达式模板实参。
- substitution 表是**有状态**的，必须按解析顺序累积；脱离上下文单独解析一个 `S_` 是没有意义的（这也解释了为什么 demangler 必须从头扫）。
- `<number>` 是**字节长度**不是字符数，非 ASCII 标识符（UTF-8 标识符）要按字节计数。
- 本 demo 不校验替换表的重复入表（文档要求"同一实体不入表两次"），真实实现需要按符号构造做规范化比较。

## 7. 注意事项与常见坑

1. **第 1 个替换是 `S_` 不是 `S0_`** —— 第 2 个才是 `S0_`，第 12 个是 `SA_`（36 进制）。
2. **`St` 不一定需要 `N...E`**：后面跟超过一个组件、或前面有 CV/ref 限定符时才需要。
3. **一元/二元同符号操作符编码不同**（`pl` vs `ps`），按符号反查会丢一半。
4. **返回类型不在符号里**（非模板函数），所以 demangle 结果里函数返回类型显示为 `Ret?` 属正常。
5. **成员函数的类型 ≠ 同名自由函数的类型**，替换表不能共用。
6. **CV 顺序**：mangling 写 `[r][V][K]`，打印反过来（const 最靠近基类型）。
7. 含 `.` 的符号（如 `_Z1fv.part.0`）是编译器私有的 clone 后缀，不是标准 mangling。

## 8. 参考资料（已读）

- [Itanium C++ ABI — §5.1 External Names (a.k.a. Mangling)](https://itanium-cxx-abi.github.io/cxx-abi/abi.html)——`<mangled-name>` / `<encoding>` 文法、`<number>` 与 base-36 `<seq-id>`（`S_` / `S0_` / `S1_` / `SA_` / `S10_` 原话示例）、`<nested-name>` 与 `[r][V][K]` / `R`|`O`、`<type>` 的 P/R/O/C/G 前缀、21 个内建类型码、操作符两字母规则与 `v<digit>` 厂商扩展、7 个 `Sx` 缩写与 `St` 的 `N...E` 例外、替换候选规则与 `_Z1fPFvvEM1SFvvE` 示例、`1AIcfE` / `N1AIT0_E1XE` / `_Z1gN3Foo1AE` 示例、abi_tag 的 `_Z1fSsB1XS_`
- 同目录 [ELF重定位计算/](../ELF重定位计算/)（demo 427）与 [DWARF调试信息解析/](../DWARF调试信息解析/)（demo 428）：符号表 → 重定位 → 调试信息这条链的上下游
