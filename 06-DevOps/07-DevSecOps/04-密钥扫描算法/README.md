# 密钥扫描算法：熵阈值、字符集上限与关键词预筛

## 简介

- 硬编码凭据（CWE-798）的自动检测几乎都落在两件事上：**先抽出候选串，再算它的"随机程度"**。本 demo 把两条主流路线放在一起对比——**Yelp/detect-secrets**（插件化、按字符集算熵）与 **gitleaks**（规则化、按 rune 算熵 + Aho-Corasick 预筛）。
- 两条路线的**熵函数不同、阈值方向一致、但漏报面完全不同**。理解差异比记住阈值重要。

## 原理详解

### 1. detect-secrets 的高熵插件

`HighEntropyStringsPlugin(charset, limit)` 只做三件事：

1. 用 ``([\'"])([<charset>]+)(\1)`` 抽出**被引号包裹**且**完全由 charset 组成**的串（反向引用 `\1` 要求首尾引号同种）；
2. 算熵；
3. 用 **严格大于** `limit` 过滤。

关键在第二步：

```python
for x in self.charset:              # ← 只遍历 charset
    p_x = float(data.count(x)) / len(data)   # ← 分母却是 len(data)
    if p_x > 0:
        entropy += -p_x * math.log(p_x, 2)
```

与教科书香农熵的差别：**不在 charset 里的字符完全不贡献**，但分母仍按整串长度算。所以 `"a!bc"` 在这套口径下是 `1.5`，而完整香农熵是 `2.0`。

> 口径提醒：正常管线里候选串已被正则限定为 charset-only，因此两者会相等；这个差异只在直接调用该函数（例如 audit 时展示熵值）时显现。

两个内置字符集：

| 插件 | charset | 默认阈值 |
| --- | --- | --- |
| `Base64HighEntropyString` | 字母数字 + `+/` + ``\-_`` + `=`（**含反斜杠**，源码写的是 `'\\-_'`） | 4.5 |
| `HexHighEntropyString` | `string.hexdigits`（22 个字符） | 3.0 |

`limit` 必须在 `0.0 ~ 8.0`，否则构造时就抛 `ValueError`。

### 2. hex 插件的纯数字惩罚

源码注释说得很直白：**全数字输入的误报远多于真阳性**。做法是：如果 `int(data)` 能成功（说明是纯数字），就减去 `1.2 / log2(len(data))`。

| 输入 | 基础熵 | 惩罚 | 结果 | 默认阈值 3.0 下 |
| --- | --- | --- | --- | --- |
| `0123456789` | 3.3219 | 0.3612 | **2.9607** | 放过 |
| `01234567890123456789` | 3.3219 | 0.2776 | **3.0443** | 上报 |
| `0123456789abcdef` | 4.0000 | 无（含字母） | 4.0000 | 上报 |

三个后果：

- 惩罚随长度**衰减**（`log2(len)` 在分母），所以"越长越可能是真阳性"——源码注释明说这是设计意图。
- `len == 1` 时不罚。
- 惩罚可以让熵变成**负数**（例如 `"55"`：0 − 1.2/1 = −1.2）。

### 3. 熵阈值的系统性漏报面

`AKIAIOSFODNN7EXAMPLE`（教科书示例 AWS key）的 base64 熵只有 **3.68**，低于默认阈值 4.5 → **默认配置直接放过**。

这不是实现 bug，而是熵判据的固有性质：**高熵 ≠ 是密钥，密钥 ≠ 高熵**。真实密钥常带厂商前缀（`AKIA`、`ghp_`、`sk-`），前缀是固定字符，会拉低熵。这就是为什么现代扫描器把「模式匹配」放在前面、「熵」只作为辅助过滤。

### 4. detect-secrets 的关键词插件

`KeywordDetector` 用一组 denylist 正则抓"变量名听起来像密钥"的赋值。几个容易忽略的点：

1. **denylist 没有词边界**：`(api_?key|...|secret|...)\w*`，所以 `not_a_secret = "x"` 也会命中（因为含 `secret`）。这是官方行为，不是 bug。
2. **按文件类型换正则集合**（`REGEX_BY_FILETYPE`）：
   - `yaml` / `ini` / `toml` / `config` / `properties` 用 `CONFIG_*`，**允许无引号**（`password: hunter2` 命中）
   - `python` / `java` / `javascript` / `swift` / `terraform` 等用 `QUOTES_REQUIRED_*`，**必须有引号**——所以 Python 里的 `password = hunter2`（无引号）**不命中**
   - `go` 额外支持 `:=`；`c` / `objc` / `csharp` 支持 `char p[25] = "x"`；`cpp` 支持 `secret("x")` 与 `.assign("x")`
3. **`keyword_exclude` 是整行级跳过**：一旦命中，该行不再产出任何结果。
4. 每次 `analyze_string` 在**同一组内**会把所有命中的正则都 yield 一遍，然后 `break` 出 `attempts` 循环。

### 5. gitleaks 的规则模型

```go
type Rule struct {
    RuleID      string
    Entropy     float64   // 0 表示关闭熵检查
    SecretGroup int       // 取第几个捕获组作为「secret」
    Regex, Path *regexp.Regexp
    Keywords    []string  // 预筛用
    Allowlists  []*Allowlist
}
```

`Validate()` 拦三类误配置：① `id` 为空；② `regex` 与 `path` 都空（规则毫无作用）；③ `secretGroup` 大于正则的捕获组数。

### 6. 熵：完整香农熵，无字符集限制

```go
charCounts := make(map[rune]int)
for _, char := range data { charCounts[char]++ }
invLength := 1.0 / float64(len(data))
for _, count := range charCounts {
    freq := float64(count) * invLength
    entropy -= freq * math.Log2(freq)
}
```

统计的是 **data 里实际出现的每个 rune**，所以 `"a!bc"` 得到 2.0。

### 7. 阈值的方向：两边一致

| 实现 | 判据 | 含义 |
| --- | --- | --- |
| detect-secrets | `entropy > self.entropy_limit` | 严格大于 |
| gitleaks | `if entropy <= r.Entropy { skip }` | 严格大于 |

**熵恰好等于阈值时两边都放过。** gitleaks 另外用 `r.Entropy != 0.0` 表示"关闭熵检查"——所以 `entropy = 0` 是"不过滤"而不是"要求熵为 0"。

### 8. 关键词预筛与 allowlist

`detect.go`：规则**没有** `keywords` 时**总是**扫描；有 `keywords` 时片段里必须命中任一关键词（实现用 Aho-Corasick trie 一次扫全部关键词）。所以一条规则的 `keywords` 配错，会让**正则本身完全跑不到**。

`allowlist` 的 `regexTarget` 决定拿哪段去匹配 allowlist 正则：`match`（整段匹配）、`secret`（只取 secret 组）、`line`（整行）。配错会让 allowlist 看起来"不生效"。

## 两套路线对比

| 维度 | detect-secrets | gitleaks |
| --- | --- | --- |
| 候选抽取 | 插件化：引号 + 字符集正则 | 每条规则自带正则 + `secretGroup` |
| 熵口径 | 只遍历 charset，分母是整串长度 | 完整香农熵，无限制 |
| 默认值 | base64 4.5 / hex 3.0 | 每条规则各自配（常见 2.0 ~ 4.5） |
| 阈值方向 | 严格大于 | 严格大于 |
| 抑制误报 | `keyword_exclude` / baseline | allowlist（path / regex / stopword / regexTarget） |
| 性能手段 | 无 | Aho-Corasick 关键词预筛 |

## 环境准备与运行

```bash
cd python && python selfcheck_secret.py    # 66 条断言，全绿打印 ALL OK
cd python && python main.py                # 打印若干对比样例
cd go && go run .
```

## 关键代码

| 文件 | 作用 |
| --- | --- |
| `python/detect_secrets.py` | 高熵插件（含 hex 惩罚）与 keyword 插件的全部正则 |
| `python/gitleaks.py` | `shannonEntropy`、`Rule.Validate`、预筛、allowlist |
| `python/main.py` | 两套扫描器的组合入口 |
| `python/selfcheck_secret.py` | 66 条断言 |
| `go/secret.go` | 同算法的 Go 转写 |

## 性能边界与注意事项

- 熵阈值**不是**安全边界：真实密钥带前缀会拉低熵，高熵串也可能是 Base64 编码的普通数据。
- **denylist 没有词边界**，`not_a_secret` 这类名字会命中。
- **keyword 插件的 filetype 分派**意味着同一行代码在不同语言文件里结论不同——跨语言仓库要留意漏报。
- gitleaks 的 `keywords` 是**前置条件**，不是提示：配错会让规则静默失效。
- 完整注意事项见 [`NOTES.md`](./NOTES.md)。

## 参考与展望

- 未完成：detect-secrets 的 **baseline**（已审计结果的持久化与合并）、transformers（在算熵前剥离 base64/hex 包装）以及 `enable_eager_search` 的无引号模式。
- 可继续：把「厂商前缀白名单」与「校验和位」（如 AWS key 的第 20 位、GitHub token 的 CRC）接进来，这是把误报压到可运维水平的关键一步。

## 参考资料（实际阅读过的权威来源）

- [Yelp/detect-secrets — `detect_secrets/plugins/high_entropy_strings.py`](https://github.com/Yelp/detect-secrets/blob/master/detect_secrets/plugins/high_entropy_strings.py) — 字符集定义、`calculate_shannon_entropy` 的 charset 遍历、hex 惩罚的 `1.2 / log(len, 2)` 与其设计意图注释、`analyze_line` 的严格大于过滤
- [Yelp/detect-secrets — `detect_secrets/plugins/keyword.py`](https://github.com/Yelp/detect-secrets/blob/master/detect_secrets/plugins/keyword.py) — `DENYLIST` 全表、`CLOSING` / `SECRET` / `AFFIX_REGEX` 各片段、`REGEX_BY_FILETYPE` 的分派表
- [Yelp/detect-secrets — `detect_secrets/plugins/base.py`](https://github.com/Yelp/detect-secrets/blob/master/detect_secrets/plugins/base.py) — 插件基类与 `analyze_line` 的通用流程
- [gitleaks/gitleaks — `config/rule.go`](https://github.com/gitleaks/gitleaks/blob/master/config/rule.go) — `Rule` 结构各字段语义与 `Validate()` 的三类校验
- [gitleaks/gitleaks — `detect/utils.go`](https://github.com/gitleaks/gitleaks/blob/master/detect/utils.go) — `shannonEntropy` 的完整实现
- [gitleaks/gitleaks — `detect/detect.go`](https://github.com/gitleaks/gitleaks/blob/master/detect/detect.go) — 关键词预筛的 `if len(rule.Keywords) == 0` 分支与熵过滤的 `entropy <= r.Entropy`
- [gitleaks/gitleaks — `config/gitleaks.toml`](https://github.com/gitleaks/gitleaks/blob/master/config/gitleaks.toml) — 内置规则的 `entropy` / `secretGroup` / `keywords` 实际取值与全局 allowlist
