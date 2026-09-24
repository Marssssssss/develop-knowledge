# 模糊查询与编辑距离自动机（Lucene FuzzyQuery / LevenshteinAutomata）

> 问题："lucne~" 凭什么能匹配到 "lucene"？Lucene 不去逐条算编辑距离，
> 而是**为查询词预先编译一个 DFA**，再拿它到词表上做一次有序遍历。
> 本文全部结论来自 `apache/lucene@main` 源码实读。

## 一、FuzzyQuery 的四个参数与四条校验

```java
public static final int  defaultMaxEdits         = LevenshteinAutomata.MAXIMUM_SUPPORTED_DISTANCE; // 2
public static final int  defaultPrefixLength     = 0;
public static final int  defaultMaxExpansions    = 50;
public static final boolean defaultTranspositions = true;
```

构造时按顺序抛三个异常（**顺序即优先级**，源码照抄）：

| 条件 | 异常 |
| --- | --- |
| `maxEdits < 0 \|\| maxEdits > 2` | `maxEdits must be between 0 and 2` |
| `prefixLength < 0` | `prefixLength cannot be negative.` |
| `maxExpansions <= 0` | `maxExpansions must be positive.` |

**`maxEdits` 硬上限是 2**，这是 `LevenshteinAutomata.MAXIMUM_SUPPORTED_DISTANCE` 决定的——
参数化描述只准备了 n=1 与 n=2 两张表。

`maxEdits == 0` 时 `getTermsEnum` 直接走 `SingleTermsEnum`（只能精确匹配），
连自动机都不建：

```java
if (maxEdits == 0) { // can only match if it's exact
  return new SingleTermsEnum(terms.iterator(), term.bytes());
}
return new FuzzyTermsEnum(terms, atts, getTerm(), maxEdits, prefixLength, transpositions);
```

## 二、相似度 → 编辑距离：一个浮点陷阱

```java
public static int floatToEdits(float minimumSimilarity, int termLen) {
  if (minimumSimilarity >= 1f) {
    return (int) Math.min(minimumSimilarity, LevenshteinAutomata.MAXIMUM_SUPPORTED_DISTANCE);
  } else if (minimumSimilarity == 0.0f) {
    return 0; // 0 means exact, not infinite # of edits!
  } else {
    return Math.min((int) ((1D - minimumSimilarity) * termLen),
                    LevenshteinAutomata.MAXIMUM_SUPPORTED_DISTANCE);
  }
}
```

三条分支，两个坑：

1. **`0.0f` 表示"精确"而不是"无限宽容"** —— 源码注释专门强调了。
   直觉上"相似度 0 = 什么都匹配"，实际是"只允许 0 次编辑"。
2. **`(int)` 是向零截断，不是四舍五入**。实测 `floatToEdits(0.8f, 10)`：
   `1 - 0.8 = 0.19999999999999996`，`× 10 = 1.9999999999999996`，截断后是 **1 而不是 2**。
   想要 2 就得把相似度调到 0.7（`1 - 0.7 = 0.30000000000000004`，`×10 = 3.0000000000000004` → 3 → 夹到 2）。

| similarity | termLen | maxEdits |
| --- | --- | --- |
| 0.0 | 10 | **0**（精确，不是无限） |
| 0.7 | 10 | 2 |
| 0.8 | 10 | **1**（浮点截断） |
| 1.0 | 10 | 1 |
| 5.0 | 10 | 2（被夹到上限） |

## 三、transpositions 开关：OSA，不是真 Damerau

`defaultTranspositions = true`，文档说"把相邻交换当作一次原子编辑"。
但 Lucene 实现的是 **OSA（Optimal String Alignment）**，不是完整的 Damerau-Levenshtein：
**一个字符只能参与一次交换**。

| 词对 | 经典 Levenshtein | OSA | 真 Damerau |
| --- | --- | --- | --- |
| `ab` → `ba` | 2 | **1** | 1 |
| `ca` → `abc` | 3 | **3** | 2 |
| `kitten` → `sitting` | 3 | 3 | 3 |

`ca → abc` 是教科书上的判别例：真 DL 允许 `ca → ac → abc`（两次，c 参与了一次交换后又参与插入），
OSA 不允许，因此给 3。

## 四、LevenshteinAutomata 的构造

```java
public LevenshteinAutomata(int[] word, int alphaMax, boolean withTranspositions) {
  // 1) 抽字母表（词里出现过的码点，去重排序）
  // 2) 算字母表在 [0, alphaMax] 上的**补集区间**
  // 3) 选参数化描述
  descriptions = new ParametricDescription[] {
      null,                                                     // n=0 用不上
      withTranspositions ? new Lev1TParametricDescription(w) : new Lev1ParametricDescription(w),
      withTranspositions ? new Lev2TParametricDescription(w) : new Lev2ParametricDescription(w),
  };
}
```

- 词里有码点 `> alphaMax` → 抛 `alphaMax exceeded by symbol N in word`。
  默认 `alphaMax = Character.MAX_CODE_POINT = 0x10FFFF`。
- 补集区间的意义：这些字符在词里**从未出现**，所以它们的特征向量恒为 0，
  自动机上可以整段共用一个转移（`addTransition(state, dest, rangeLower[r], rangeUpper[r])`）。
  以 `"abca"` 为例，字母表 `[97,98,99]`，补集是 `[0,96]` 与 `[100, 1114111]` 两段。

## 五、参数化描述：状态数是"表长 × (w+1)"

```java
int size() { return minErrors.length * (w + 1); }

boolean isAccept(int absState) {
  int state  = absState / (w + 1);
  int offset = absState % (w + 1);
  return w - offset + minErrors[state] <= n;
}

int getPosition(int absState) { return absState % (w + 1); }
```

`minErrors` 是**编译期写死的常量表**（源码里逐行列出）：

| 描述 | n | minErrors | 长度 | size(w=4) |
| --- | --- | --- | --- | --- |
| `Lev1ParametricDescription` | 1 | `0,1,0,-1,-1` | 5 | 25 |
| `Lev1TParametricDescription` | 1 | `0,1,0,-1,-1,-1` | 6 | 30 |
| `Lev2ParametricDescription` | 2 | 30 项（含 `-2`） | 30 | 150 |

于是 **`n=1` 的 DFA 状态数与词长成线性（5·(w+1)），与字母表大小无关** —— 这就是
"编辑距离自动机"能实用的原因。

`isAccept` 里 `minErrors` 的负数不是"错误数"，而是**下界标记**：
`w - offset + minErrors[state] <= n` 这式子对 `-1` / `-2` 的 state 在某些 offset 下也成立
（例如 `w - offset - 1 <= 1` 在 `offset >= w-2` 时为真），所以负数项**也可能落在接受态里**。
本 demo 照抄公式，未做"看起来更合理"的改写。

## 六、toAutomaton 的几个可判定量

```java
if (n == 0) return Automata.makeString(prefix + UnicodeUtil.newString(word, 0, word.length));
if (n >= descriptions.length) return null;          // 不支持 n >= 3
final int range            = 2 * n + 1;
final int numStates        = description.size();
final int numTransitions   = numStates * Math.min(1 + 2 * n, alphabet.length);
```

- `n=0` 退化成一条**字符串自动机**（`prefix + word`），没有任何模糊。
- `n>=3` 直接 `return null`。
- **`numTransitions` 取 `min(1+2n, |alphabet|)`**：字母表比 `2n+1` 还小时，
  转移数由字母表规模决定，而不是按"每状态最多 2n+1 条"估算。

特征向量 `getVector(x, pos, end)` 是**先左移再判等置位**：

```java
int vector = 0;
for (int i = pos; i < end; i++) { vector <<= 1; if (word[i] == x) vector |= 1; }
return vector;
```

所以**最早进入窗口的字符落在最高位**。以 `"abca"`、`pos=0`、`end=3` 为例：

| x | 向量 |
| --- | --- |
| `a` | `100` |
| `b` | `010` |
| `c` | `001` |
| `z`（不在词里） | `000` |

窗口长度是 `min(w - pos, 2n+1)`，在词尾会被夹住。

## 七、本 demo 复现的现象

| 现象 | 验证点 |
| --- | --- |
| 编辑距离硬上限 2 | `MAXIMUM_SUPPORTED_DISTANCE == 2`，`maxEdits=3` 抛异常 |
| `maxEdits=0` 不建自动机 | `terms_enum_kind == "SingleTermsEnum"` |
| `similarity=0` 是精确不是无限 | `float_to_edits(0.0, 10) == 0`；浮点截断见 §二 |
| OSA 与真 DL 的差别 | `ca→abc`：OSA=3（真 DL=2） |
| transpositions 只影响交换 | `ab→ba`：1 vs 2 |
| 字母表去重排序 + 补集两段 + `alphaMax` 越界 | `abca` → `[97,98,99]`，补集 `[0,96]`/`[100,1114111]` |
| 状态数 = 表长 ×(w+1) | 5/6/30 × 5 |
| `n>=3` 不支持 / `numTransitions` 取 min | `unsupported`；`min(2n+1, |alphabet|)` |
| 特征向量"先左移后置位" | `X(a,0,3) = 100` |

> **建模口径与限制**：真正的 DFA 转移表（`toStates*` / `offsetIncrs*` 打包数组）**未还原**，
> 本 demo 断言的是参数化描述的**骨架**与 FuzzyQuery 的参数语义，均可逐行对上源码。
> `Lev2TParametricDescription` 本轮未取到源码（jsDelivr 超时），故 n=2 一律用 `Lev2` 的骨架，
> n=2 且开启 transpositions 时状态数/接受判据只对非 T 变体严格成立。
> 距离函数只作对照，不是 Lucene 的判定路径（Lucene 走自动机，不逐条算距离）。

## 八、参考资料（实际读过）

- `apache/lucene@main` — `lucene/core/src/java/org/apache/lucene/search/FuzzyQuery.java`（10929 B）
- `lucene/core/src/java/org/apache/lucene/util/automaton/LevenshteinAutomata.java`（11921 B）
- `lucene/core/src/java/org/apache/lucene/util/automaton/Lev1ParametricDescription.java`（9033 B）
- `lucene/core/src/java/org/apache/lucene/util/automaton/Lev2ParametricDescription.java`（10563 B）
- `lucene/core/src/java/org/apache/lucene/util/automaton/Lev1TParametricDescription.java`（4117 B）
- `lucene/core/src/java/org/apache/lucene/util/automaton/Operations.java`（`DEFAULT_DETERMINIZE_WORK_LIMIT = 10000`）

## 九、文件

`python/fuzzy.py`(213) 校验/floatToEdits/参数化描述/自动机骨架 · `python/editdist.py`(60) 与 `go/editdist.go`(92) 距离对照 · `python/selfcheck_fuzzy.py`(204) **111 条断言实跑全绿** · `python/main.py`(92) · `go/fuzzy.go`(253) · `go/main.go`(74)。

> Go 侧无本机工具链，走 `bracket_check` / `go_sanity` / `go_crossref` 三项静态检查，全过。
