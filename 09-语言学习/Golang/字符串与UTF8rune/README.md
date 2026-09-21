# Go 字符串、字节与 rune（unicode/utf8）

## 简介

Go 的 `string` 是**只读字节序列**，`len(s)` 给的是字节数而不是字符数；
`for range s` 才会按 UTF-8 解码出一个个 `rune`（= Unicode 码点）。
这层「字节 vs 码点」的分离，全部实现在 `unicode/utf8` 包的一张 256 项查表里。

关键概念：

| 概念 | 一句话 |
| --- | --- |
| `rune` | `int32` 的别名，一个 Unicode 码点 |
| `RuneError = U+FFFD` | 解码失败的替换字符；非法序列返回 `(RuneError, 1)` |
| `first [256]uint8` | 首字节信息表：高 4 位 = 第二个字节的合法区间下标，低 4 位 = 序列长度 |
| `acceptRanges` | 5 个区间，专门用来拦 overlong / 代理区 / 超上限 |
| `RuneSelf = 0x80` | 小于它是 ASCII，单字节 |

## 原理详解

### 1. 一张表决定一切

官方把首字节的信息压成一个字节：

```
高 4 位：acceptRanges 的下标（F 表示「ASCII 或非法」这类单字节特例）
低 4 位：序列长度（或特例的 status）
```

分区（自检 E2 逐段核对）：

| 首字节 | 记号 | 含义 |
| --- | --- | --- |
| `00-7F` | `as` | ASCII，size 1 |
| `80-BF` | `xx` | 非法：续字节不能当首字节 |
| `C0-DF` | `s1` | 2 字节，accept 0 |
| `E0` | `s2` | 3 字节，accept 1 |
| `E1-EC` | `s3` | 3 字节，accept 0 |
| **`ED`** | **`s4`** | 3 字节，accept 2 ← **靠它拦代理区** |
| `EE-EF` | `s3` | 3 字节，accept 0 |
| `F0` | `s5` | 4 字节，accept 3 |
| `F1-F3` | `s6` | 4 字节，accept 0 |
| **`F4`** | **`s7`** | 4 字节，accept 4 ← **靠它拦 > U+10FFFF** |
| `F5-FF` | `xx` | 非法 |

### 2. 三类非法序列都是「第二个字节」拦下来的

```go
var acceptRanges = [16]acceptRange{
    0: {locb, hicb},   // 0x80-0xBF 默认
    1: {0xA0, hicb},   // E0 开头：第二个字节必须 >= A0，否则是 overlong
    2: {locb, 0x9F},   // ED 开头：必须 <= 9F，否则落在代理区 U+D800..DFFF
    3: {0x90, hicb},   // F0 开头：必须 >= 90，否则是 overlong
    4: {locb, 0x8F},   // F4 开头：必须 <= 8F，否则超过 U+10FFFF
}
```

实测（自检 E3）：

| 字节序列 | Go 的结果 | 被谁拦住 |
| --- | --- | --- |
| `E0 80 80` | `(RuneError, 1)` | accept 1，overlong |
| `ED A0 80`（U+D800） | `(RuneError, 1)` | accept 2，代理区 |
| `F4 90 80 80`（U+110000） | `(RuneError, 1)` | accept 4，超上限 |
| `F0 80 80 80` | `(RuneError, 1)` | accept 3，overlong |
| `ED 9F BF`（U+D7FF） | `(0xD7FF, 3)` | 边界内侧，合法 |
| `F4 8F BF BF`（U+10FFFF） | `(0x10FFFF, 4)` | 边界内侧，合法 |

> 也就是说：**Go 判断「是不是合法 UTF-8」的关键不在于首字节，而在于第二个字节落在哪个区间。**

### 3. 非法输入不报错，而是 `(RuneError, 1)`

这是最容易踩的一条：

- 空输入 → `(RuneError, 0)`（**唯一 size=0 的情况**）
- 任何非法/截断输入 → `(RuneError, 1)`（size 是 **1**，不是 0）

对比：Python 的 `bytes.decode()` 抛 `UnicodeDecodeError`，Go 替换字符继续走。
`for range` 与 `DecodeRuneInString` 的迭代序列被官方定义为**完全一致**
（[Strings, bytes, runes and characters in Go](https://go.dev/blog/strings)）：
「The for range loop and DecodeRuneInString are defined to produce exactly the same iteration sequence.」

所以 `RuneCountInString`（官方实现就是 `for range s { n++ }`）在遇到 `\x80\x80` 时给出 **2**——
每个非法字节各算一个 rune。

### 4. RuneLen 的代理区夹在中间

```go
func RuneLen(r rune) int {
    switch {
    case r < 0:                                  return -1
    case r <= rune1Max:                          return 1   // <= 0x7F
    case r <= rune2Max:                          return 2   // <= 0x7FF
    case surrogateMin <= r && r <= surrogateMax: return -1  // 0xD800-0xDFFF
    case r <= rune3Max:                          return 3   // <= 0xFFFF
    case r <= MaxRune:                           return 4
    }
    return -1
}
```

注意代理区判断**在 `rune2Max` 之后、`rune3Max` 之前**：`U+D800` 数值上 ≤ `0xFFFF`，
如果把它写在最后就会返回 3 而不是 -1。实测 `U+D7FF → 3`、`U+D800 → -1`、`U+E000 → 3`。

`EncodeRune` 对**负数、代理区、超上限**三类一律写 `RuneError` 的编码 `EF BF BD`。

### 5. len vs rune 数

官方博客结论：「Strings are built from bytes so indexing them yields bytes, not characters.」

| 表达式 | `"世界"` 的结果 |
| --- | --- |
| `len(s)` | 6（字节） |
| `utf8.RuneCountInString(s)` | 2（rune） |
| `for i, r := range s` | 迭代 2 次，`i` 取 0 和 3（**字节下标**） |

`%q` 转义不可打印字符，`%+q` 连非 ASCII 一起转义（官方博客里用日本语例子演示）。

## 对比 / 选型

| 语言 | 非法输入的行为 | 「长度」默认语义 |
| --- | --- | --- |
| Go | `(RuneError, 1)` 继续走 | 字节数 |
| Python | 抛 `UnicodeDecodeError` | 码点数（`len(str)`） |
| Rust | `from_utf8` 返回 `Err`；`from_utf8_lossy` 换 U+FFFD | 字节数 |

Go 的选择让「有脏数据也不会崩」，代价是**非法字节会静默变成 U+FFFD**，做校验时必须显式调 `utf8.Valid`。

## 环境准备

- Go 1.21+；Python 3.8+；无第三方依赖

## 运行方式

```bash
cd python && python selfcheck_utf8.py   # 88 条断言（含全量码点对拍）
cd python && python main.py             # 六组结论
cd go && go run .                       # 九组结论，内置 check 断言
```

`selfcheck_utf8.py` 的 E4 会把**全部 1 114 112 个合法码点**（跳过代理区）用模型编码，
与 Python 自己的 UTF-8 codec 逐字节比对；E5 再做抽样解码对拍。

## 关键代码片段

`go/utf8.go` 里最核心的 12 行（第二个字节的区间校验）：

```go
sz := int(x & 7)
accept := acceptRanges[x>>4]
if n < sz {
    return Rune{runeError, 1}          // 输入被截断
}
b1 := p[1]
if b1 < accept.lo || accept.hi < b1 {
    return Rune{runeError, 1}          // overlong / 代理区 / 超上限
}
if sz <= 2 {
    return Rune{rune(p0&mask2)<<6 | rune(b1&maskx), 2}
}
```

## 性能与边界

- `UTFMax = 4`，`MaxRune = 0x10FFFF`（Unicode 上限，不是 `int32` 上限）
- `first` 表 256 字节、`acceptRanges` 16 项，全部静态数据，解码是 O(1) 查表
- `RuneCountInString` 是 O(n) 单次扫描，**不要**在循环里反复调用

## 注意事项与常见坑

1. **`len(s)` 是字节数**：需要字符数就调 `utf8.RuneCountInString`，但它也是 O(n)。
2. **非法字节不会报错**：结果里出现 U+FFFD 说明输入脏了；`utf8.Valid` 才是判定入口。
3. **截断序列算 1 个 rune**：`RuneCountInString("\xe4\xb8")` = 2，不是 0 也不是 1。
4. **`s[i]` 取到的是 byte 不是 rune**：想按字符索引先转 `[]rune`（会产生一次完整拷贝）。
5. **`RuneLen` 对代理区返回 -1**：手写 UTF-8 编码器时最容易漏这一档。
6. **字符串比较是字节比较**：Unicode 规范化（NFC/NFD）不在标准库语义里，
   官方博客明说「No guarantee is made in Go that characters in strings are normalized」。
7. 与 Python 对拍时要记得：Python 的 `str` 是码点序列，`bytes.decode()` 遇到非法就抛；
   Go 的 `string` 允许装任意字节，解码永远成功（失败给替换字符）。

## 参考资料（实际阅读过的权威来源）

- [Go 源码 src/unicode/utf8/utf8.go](https://github.com/golang/go/blob/master/src/unicode/utf8/utf8.go) — `RuneError/RuneSelf/MaxRune/UTFMax`、四个 mask 与 tag、`first` 256 项表、`acceptRanges`、`DecodeRune/decodeRuneSlow`、`RuneLen`、`EncodeRune`、`RuneCountInString`、`RuneStart`、`Valid`
- [Go 官方博客 Strings, bytes, runes and characters in Go](https://go.dev/blog/strings) — 「indexing yields bytes, not characters」、`for range` 解码 rune、`%q`/`%+q` 转义行为、「for range 与 DecodeRuneInString 迭代序列一致」、字符串不保证 Unicode 规范化
- [Go 官方包文档 unicode/utf8](https://pkg.go.dev/unicode/utf8) — 各函数的公开契约与 `(RuneError, 1)` 的返回约定
