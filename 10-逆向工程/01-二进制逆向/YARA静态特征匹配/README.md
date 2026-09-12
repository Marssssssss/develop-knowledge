# YARA 静态特征匹配(规则语法 + 匹配引擎原理)

> YARA 是恶意样本归类的行业事实标准("样本界的 grepg")。本 demo 双实现:`mini_yara.py` 用纯 Python 复刻其规则子集的**匹配引擎语义**(文本/xor/wide/十六进制通配/跳变/条件量词),`fake_backdoor.yara` 是一份**可直接被真实 yara 命令执行**的等价规则。

## 简介

- **实现**:`mini_yara.py`(教学引擎)+ `fake_backdoor.yara`(真实 YARA 规则)
- **语言**:Python
- **依据**:[YARA 官方文档(writing-rules)](https://yara.readthedocs.io/en/stable/writingrules.html) + [YARA 官网示例规则 silent_banker](https://virustotal.github.io/yara/)

## 原理详解

### 规则四段式结构(官方 silent_banker 示例的骨架)

```yara
rule 名称 : 标签 {
    meta:      // 元信息,不参与匹配(description / threat_level / in_the_wild)
    strings:   // $a = {6A 40 68 00 30 00 00 ...}  $b = "文本"
    condition: // $a or $b or $c  —— 布尔表达式决定命中
}
```

### 字符串声明的三大家族

**① 文本字符串 + 修饰符**(每项语义均来自官方文档):

| 修饰符 | 语义 | 备注 |
| --- | --- | --- |
| `nocase` | 忽略大小写(foobar 匹配 Foobar/FOOBAR) | **不能与 xor 同用** |
| `wide` | 字节交错 `\x00` 模拟宽字符(`B\x00o\x00...`) | 不是真 UTF-16 编码 |
| `ascii` | 与 wide 连用表示"两种都匹配";单独用无意义(默认即 ASCII) | — |
| `xor` | 为**每个单字节密钥**生成一个变体(等价 255 条规则) | 不能与 nocase 同用;`xor(0x01-0xff)` 可限定密钥范围 |
| `fullword` | 前后必须是非字母数字字符(`domain` 不匹配 mydomain.com) | — |

**② 十六进制字符串**:半字节级通配 `??`(整字节)与 `A?`(高半字节确定);跳变 `[4-6]` 匹配 4-6 字节任意序列、`[6]` 固定长度、`[10-]` 无上界。官方例子:`{ F4 23 [4-6] 62 B4 }` 能同时命中 `F4 23 01 02 03 04 62 B4` 与 `F4 23 00 00 00 00 00 62 B4`。

**③ 正则表达式**:`/md5: [0-9a-fA-F]+/`,本 demo 未实现。

### 条件层:匹配结果的"查询语言"

- `$x`(出现即真)、`#x == 6`(恰出现 6 次)、`@x[1]`(第 1 次出现的偏移,索引从 1 起)、`$x at 100` / `$x in (0..100)`(位置约束);
- 集合量词:`2 of ($a,$b,$c)`、`any of them`、`all of ($foo*)`、`none of ($b*)`;
- `filesize > 200KB`(KB=1024, MB=2²⁰;**仅文件扫描有效**,扫进程内存永假);
- 模块(本 demo 未实现):`import "pe"` 后可用 `pe.entry_point == 0x1000`、`for any section in pe.sections : (section.name == ".text")`、`pe.imphash() == "..."` —— 与同目录 PE 文件解析 demo 直接衔接。

### 引擎实现的三层结构(mini_yara.py 的对应)

1. **模式展开层** `StrDef.candidates()`:`xor` 展开 255 个密钥变体、`wide` 生成交错形态 —— 官方文档明确说明 `xor` 的等价展开写法(XorExample2);
2. **匹配层** `find_all / match_hex`:文本朴素查找;十六进制递归回溯支持通配与跳变;
3. **求值层** `scan()`:缓存每条字符串的 occurrences,条件以 `#`/`@`/`at` 查询,只做布尔组合。

## 运行方式

```bash
python mini_yara.py                 # 构造假样本 → 扫描 → 输出命中分布与 MATCH 判定
yara fake_backdoor.yara <目标文件>   # 真实 YARA 对照(需安装 yara)
```

## 关键代码说明

- `match_hex()` 中跳变 `[4-6]` 用 `rec(pos+skip, ...)` 枚举所有跳过长度 —— 变长跳变让匹配点呈指数分支,这正是真实 YARA 在文档里强调"避免大范围跳变"的原因。
- xor 展开先算其他修饰符(如 wide)再整体异或 —— 与官方 XorExample4 的"对交错后的字节也应用 XOR"语义一致。

## 性能边界

- 朴素查找 O(n·m) + xor 展开 255 倍模式数;真实 YARA 文本与十六进制模式合成**单遍多模式匹配**(Aho-Corasick 类自动机),复杂度近似 O(n)。
- 十六进制跳变区间过大(如 `[1000-2000]`)会拖垮扫描 —— 官方文档有效示例的下界建议从紧。

## 注意事项与常见坑

1. **`wide` 不等于 UTF-16**:只是 ASCII 交错零字节,中文等多字节内容需要显式 hex 或正则。
2. **`@a[i]` 越界返回 NaN** 而非报错,条件里直接比较会得到出乎意料的假值。
3. **条件里引用未命中的字符串**:`#` 为 0、`@` 为 -1(mini 版)—— 官方是 undefined,写规则时务必先判存在再取偏移。
4. **已弃用 `entrypoint` 变量**(YARA 3.0 起警告):应改用 `pe.entry_point`。
5. **规则质量 > 规则数量**:高熵随机串、过短字符串(<4 字节)会产生海量误报;官方生态用 YARA-CI 持续回归测试规则误报率。

## 参考资料(实际读过)

- [Writing YARA rules — YARA 官方文档(yara.readthedocs.io)](https://yara.readthedocs.io/en/stable/writingrules.html):全部修饰符语义与限制表、十六进制通配/跳变、`#`/`@`/`!`/`at`/`in`、`for..of` 量词、filesize、PE 模块示例
- [YARA 官网(virustotal.github.io/yara)](https://virustotal.github.io/yara/):silent_banker 完整示例规则(四段式结构的权威范本)
