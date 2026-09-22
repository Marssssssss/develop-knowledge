# CVSS v4.0 评分：宏向量查表与类内插值

## 一、简介

CVSS（Common Vulnerability Scoring System）v3.1 的 Base/Threat/Environmental 是**闭式公式**：
把指标取值代进 `Iss`、`Impact`、`Exploitability` 一串算术式就得到分数。v4.0 把这整套推倒重来 ——
**没有公式了**。

v4.0 的做法是：专家组先对所有可能的指标组合做定性排序，把「严重度相当」的向量聚成若干
等价类（**MacroVector**，宏向量），给每个等价类查表定一个分数；类内的向量再按
「离本类最高严重度向量有多远 / 本类深度」的比例做**插值**。分数因此是
`查表值 - 平均比例距离`，而不是算出来的。

本 demo 把官方参考实现 `FIRSTdotorg/cvss-v4-calculator` 的评分逻辑完整转写成 Python 与 Go，
并用**官方 JS 实现跑出来的结果**做期望值对拍。

## 二、六个等价类（规范 Table 24-30）

宏向量是 6 位数字串，每一位是一个指标子组（EQ）的层级，**0 最严重**：

| 位 | 指标子组 | 层级 0 | 层级 1 | 层级 2 |
| --- | --- | --- | --- | --- |
| EQ1 | AV / PR / UI | `AV:N 且 PR:N 且 UI:N` | 三者至少一个最优，且 `AV != P` | `AV:P` 或三者都不最优 |
| EQ2 | AC / AT | `AC:L 且 AT:N` | 其余（含 `AC:L/AT:P`、`AC:H/AT:N`） | — |
| EQ3 | VC / VI / VA | `VC:H 且 VI:H` | 有 H 但不同时 VC/VI 都 H | 一个 H 都没有 |
| EQ4 | SC / SI / SA（含 MSI/MSA） | `MSI:S 或 MSA:S` | 无 S 但有 H | 无 S 也无 H |
| EQ5 | E | `E:A` | `E:P` | `E:U` |
| EQ6 | CR / IR / AR 与 VC / VI / VA | `(CR:H 且 VC:H)` 或 `(IR:H 且 VI:H)` 或 `(AR:H 且 VA:H)` | 其余 | — |

几个容易写反的点：

- **EQ1 的 `AV:P` 是一票否决**：只要 `AV:P`，不管 PR/UI 多宽松都直接落到层级 2。
- **EQ3 只看 VC/VI/VA**，不看 SC/SI/SA —— 后续系统影响单独归 EQ4。
- **EQ3 与 EQ6 不独立**，规范用联合表（Table 30）处理，其中 `EQ3=2 且 EQ6=0` 标注为 **Cannot exist**
  （一个 H 都没有，就不可能有「高要求撞上高影响」），查表里也没有 `002000` 这类键。
- **SC 没有 `S` 取值**，只有 `H/L/N`；`S`（Safety）只给 `SI`、`SA` 以及修正指标 `MSI`、`MSA`。
  因此 `CVSS:4.0/.../SC:S/...` 是非法向量，`parse_vector` 会拒绝。

规范正文说「MSI/MSA 为 X 时回落到 SI/SA」，而官方 `cvss_score.js` 的 `m()` 对 `MSI:X` 直接返回 `"X"`。
两者不矛盾：`SI`/`SA` 永远取不到 `S`，回落后的取值同样进不了 EQ4=0。本 demo 按规范口径实现
（见 `_msi_msa`），并与官方实现在 690 条随机向量上逐条对齐。

## 三、插值算法（官方 cvss_score.js）

```
1. 短路：VC/VI/VA/SC/SI/SA 全为 N  ->  0.0
2. value = lookup[宏向量]                      # 本类最高严重度向量的分数
3. 对每个 EQ：
     available = value - lookup[下一个更低宏向量]   # 不存在则 NaN，整项忽略
     dist      = 当前向量到本类最高严重度向量的严重度距离（各指标层级差之和）
     depth     = 本类深度（max_severity.js） * 0.1
     term      = available * (dist / depth)
   —— EQ5 例外：官方把比例恒定为 0，只贡献一个 0 到分子、但计入分母个数
4. score = value - mean(所有非 NaN 的 term)，截断到 [0,10]，保留一位小数
```

「下一个更低宏向量」不是简单的每一位 +1：EQ3+EQ6 是一张联合跳转表（官方代码里
`(0,0)` 有两条下坡路，取分数更高的那条；`(2,1)` 的下坡是 `(3,2)`，不存在 → NaN）。

### 三个反直觉结论

1. **威胁成熟度 E 只换查表值，不参与插值。** 同一条向量 `E:A / E:P / E:U` 得到 10.0 / 9.8 / 9.5，
   差值全部来自 `lookup`，插值项贡献恒为 0。
2. **当 VC/VI/VA 都不是 H（EQ3=2）时，安全要求 CR/IR/AR 完全不起作用。**
   因为下一级是 `(3,2)`，不存在 → `available` 是 NaN → 整个 EQ3+EQ6 项被忽略。
   实测 `CR/IR/AR = HHH / MMM / LLL` 三条向量分数都是 6.9。作为对照，影响是 H 时
   同样的改动会把 10.0 拉到 9.6。
3. **环境修正能把「没有后续影响」的漏洞顶到 10.0。** 基础 `SC:N/SI:N/SA:N` 的漏洞，
   加上 `MSC:H/MSI:S/MSA:S` 后宏向量从 `000200`（9.3）跳到 `000000`（10.0）——
   这才是 v4.0 把 Environmental 指标做成「覆盖」而不是「加权」的实际后果。

## 四、为什么改成查表

规范 §8.2 给的理由很直白：v3.1 的公式会出现「指标变严重但分数不变」的平局，也会出现
分数分布挤在某个区间的问题。v4.0 用专家排序 + 查表，保证**任何一处指标变差都能（尽可能）
让分数变动**，同时把定性分级边界（4.0 / 7.0 / 9.0）与分数分布对齐。

代价是分数**不可手算**：270 条查表 + 一张深度表 + 一套跳转规则，只能靠参考实现。
`lookup` 只有 270 条而不是 3×2×3×3×3×2=324 条，缺口正是那些「不可能组合」。

## 五、代码结构

| 文件 | 内容 |
| --- | --- |
| `python/cvssdata.py` | 层级表、合法取值、270 条查表、最高严重度向量、深度表 |
| `python/cvss.py` | 解析、默认值 `resolve()`、EQ 判定、宏向量、插值、定性分级 |
| `python/selfcheck_cvss.py` | 自检（796 条断言） |
| `python/main.py` | 演示 |
| `go/cvssdata.go` `go/eq.go` `go/cvss.go` `go/main.go` | 同构 Go 实现（本机无 Go 工具链，走人工审查 + 三项静态检查） |

## 六、验证方式

1. **与官方实现对拍**：把 690 条随机/定向向量分别喂给 `node` 加载的官方
   `cvss_score.js` 与本 demo 的 Python 实现，宏向量与分数（一位小数）**零偏差**。
2. 自检里固化了其中 25 条官方输出（标注 `[官方]`），加上 EQ 判定边界、默认值规则、
   解析校验、定性分级边界、插值支配性等结构性断言，共 **796 条全绿**。
3. Go 侧无工具链，用 `bracket_check` / `go_sanity` / `go_crossref` 做机械核查，全部通过。

## 七、参考资料（均为本轮实际读取）

- CVSS v4.0 规范正文：https://www.first.org/cvss/specification-document
  （§8 MacroVectors 与插值、Table 24-30、§7 定性分级）
- 官方参考实现（本 demo 的转写对象）：
  - https://github.com/FIRSTdotorg/cvss-v4-calculator/blob/main/cvss_score.js
  - https://github.com/FIRSTdotorg/cvss-v4-calculator/blob/main/cvss_lookup.js
  - https://github.com/FIRSTdotorg/cvss-v4-calculator/blob/main/max_composed.js
  - https://github.com/FIRSTdotorg/cvss-v4-calculator/blob/main/max_severity.js
  - https://github.com/FIRSTdotorg/cvss-v4-calculator/blob/main/metrics.js
- 指标机器可读定义：https://www.first.org/cvss/cvss-v4.0.json
