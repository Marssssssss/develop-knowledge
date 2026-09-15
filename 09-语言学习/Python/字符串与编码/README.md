# 字符串柔性表示(PEP 393)与编码

## 简介

- Python 3 的 `str` 是 Unicode 码点序列;3.3 起(PEP 393)内部表示**按字符串中最大码点
  动态选择 1/2/4 字节档**——取代编译期固定 UCS-2/UCS-4 的旧构建,ASCII 串重新回到 1 B/字符。
- 编码(`str` → `bytes`)与解码(`bytes` → `str`)是**显式**的:UTF-8 变长 1~4 字节,
  默认 strict 模式对非法序列报 `UnicodeEncodeError` / `UnicodeDecodeError`。
- 关键概念:
  - **kind**:内部存储档位,由 `maxchar` 决定(本 demo 用 `sys.getsizeof` 斜率反推)
  - **compact 布局**:字符数据**内联**在对象头之后,无独立数据指针、不可 resize
  - **maxchar**:字符串中最大码点,决定 kind 的唯一依据
  - **驻留(intern)**:标识符风格的字符串进全局表共享(编译器自动/`sys.intern` 手动)

## 原理详解

### 1. 三档 kind(PEP 393)

| kind | 每字符 | 触发条件(由 maxchar) |
| --- | --- | --- |
| 1 字节 | 1 B | `maxchar < 256`(ASCII 是其特例,结构头更小) |
| 2 字节 | 2 B | `256 ≤ maxchar < 0x10000`(BMP) |
| 4 字节 | 4 B | 含非 BMP 字符(emoji 等) |

- **一个字符拉满整串**:`"a"*999 + "😀"` 整串升到 4 B/字符(本 demo 实测)。
- 拼接按结果重算 kind;与空串拼接不变档(无升档场景)。
- **收益(PEP 393 原文数据)**:Django 实测 Unicode 存储从宽构建 6,378,540 B 降到
  2,216,807 B(降 65%);36,000 个字符串里 35,713 个是 ASCII。
- **代价**:遍历 API 复杂化(`PyUnicode_KIND/READ/WRITE` 宏),移植后基准慢 1%–30%。

### 2. compact 与非 compact 布局

```text
PyASCIIObject            → length, hash, state{kind, compact, ascii, interned}, wstr
 └ PyCompactUnicodeObject → + utf8_length, utf8, wstr_length
      └ PyUnicodeObject   → + data 联合体指针(latin1/ucs2/ucs4)
```

- **compact(主流)**:创建时已知 size 与 maxchar,数据紧跟对象头内联;
  ASCII 用最小头的 `PyASCIIObject`,latin-1 用 `PyCompactUnicodeObject`
  (本 demo 实测两者头 42 B vs 60 B);不支持 resize。
- `data` 与 `utf8` 指向同一内存 ⟺ 字符串为**纯 ASCII**(仅 latin-1 不够)。
- `PyUnicode_AsUTF8` 惰性计算 UTF-8 并缓存到对象释放——能省则用 `AsUTF8String`。
- **索引 O(1)** 是等宽存储的直接收益(旧 narrow 构建里非 BMP 字符是代理对两单元)。

### 3. UTF-8 编码(外部表示)

| 码点范围 | UTF-8 字节 |
| --- | --- |
| < 0x80 | 1 B |
| < 0x800 | 2 B(如 `é` → `C3 A9`) |
| < 0x10000 | 3 B(如 `中` → `E4 B8 AD`) |
| ≥ 0x10000 | 4 B(如 `😀` → `F0 9F 98 80`) |

- `str.encode("latin-1")` 装不下 BMP 字符 → `UnicodeEncodeError`(e.encoding 可读)。
- **孤立代理项**(`"\ud800"`)在 strict UTF-8 下报错;`surrogatepass` 错误策略放行。
- 解码错误对象 `UnicodeDecodeError` 带 `start`/`end`/`reason`;
  3.13 实测坑:C0 序列后接非接续字节,报的是**首字节位置** 0(不是坏字节位置 1)。
  `errors="replace"` → U+FFFD;`"ignore"` → 丢弃。

### 4. 码点语义与驻留

- 比较/`len`/`max`/`min`/索引都按**码点**,无区域设置参与:`"Z" < "a"`、`"z" < "é"`。
- 驻留:编译期标识符与常量折叠的字符串同一对象(本 demo 实测 `"identi"+"fier"
  is "identifier"`);**非标识符**的动态构造串不自动驻留,`sys.intern` 手动入表后共享。

## 对比 / 选型

| 表示 | 每字符内存 | 索引 | 适用 |
| --- | --- | --- | --- |
| PEP 393 (str) | 1/2/4 B 动态 | O(1) | 一切文本 |
| UTF-8 bytes | 1~4 B | O(n) 扫描 | IO/网络/文件 |

## 环境准备

- OS:任意(纯标准库)
- Python:3.3+(PEP 393);实测 3.13.14
- 依赖:无

## 运行方式

```bash
python3 main.py
```

## 关键代码片段

```python
def bytes_per_char(s):
    """斜率法推 kind:差分避开不同档结构头大小不一的干扰。"""
    n = max(len(s), 1)
    return (sys.getsizeof(s * 2) - sys.getsizeof(s)) / n

assert bytes_per_char("a" * 1000) == 1.0      # ASCII → 1B
assert bytes_per_char("中" * 1000) == 2.0     # BMP → 2B
assert bytes_per_char("😀" * 1000) == 4.0     # 非 BMP → 4B
```

## 性能与边界

- 内存:Django 实测降 40%–65%;ASCII 紧凑串开销几乎不随长度增长(数据内联)。
- 遍历:kind 判断把简单循环变成三路分支——PEP 393 移植基准慢 1%–30% 的原因。
- 限制:`sys.getsizeof` 只见对象本身,不看 intern 表共享带来的节省。

## 注意事项与常见坑

- **坑:拿 `getsizeof(s) - getsizeof("")` 推字符宽** —— 不同 kind 的对象头大小不同
  (latin-1 头比 ASCII 大 18 B),要用**斜率差分**(本 demo 开发期实测踩中)。
- **坑:`"hello" + "!"` 验证驻留失败** —— 常量折叠使两次拼接是同一编译期常量;
  须经变量/函数在运行期构造(本 demo 实测)。
- **坑:期望 UnicodeDecodeError.start 指向坏字节** —— CPython 对"接续字节缺失"
  报的是**首字节**位置(3.13 实测)。
- **坑:latin-1 想当然** —— 它只能编码 0–255,`"中".encode("latin-1")` 直接抛错。
- 运行期修改默认编码相关行为(如 `PYTHONUTF8`)影响 IO 默认值,但不改变上述内存模型。

## 参考资料(实际阅读过的权威来源)

- [PEP 393 – Flexible String Representation](https://peps.python.org/pep-0393/) —
  三档 kind、compact 布局、头结构层级、Django 实测数据、1%–30% 性能代价原文。
- [Unicode HOWTO(Python 官方文档)](https://docs.python.org/3/howto/unicode.html) —
  编解码语义、错误处理策略与码点语义(关联阅读)。
