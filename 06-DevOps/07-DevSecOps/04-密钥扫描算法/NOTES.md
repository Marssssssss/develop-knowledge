# NOTES — demo574 口径说明与踩坑

## 一、口径说明（本 demo 自己选的读法）

1. **`CLOSING` 的转写**：源码是 `r'[]\'"]{0,2}'`，字符类里是 `]`、`'`、`` " ``。
   初版转写多带了一层转义把**反斜杠**也放进字符类，属于转写错误，已修正。
2. **C 语言系列正则的分组编号**：`FOLLOWED_BY_EQUAL_SIGNS_OPTIONAL_BRACKETS_...`
   源码里写 `(\5)` 而 `COMMON_C_DENYLIST_REGEX_TO_GROUP` 给的是 **6**。
   原因是 `SQUARE_BRACKETS` 自身带一个捕获组、外面又套了一层 `(...)?`，
   实际共 7 个组，secret 在第 6 组。**实测 `regex.groups` 与 `match.groups()` 已确认**
   ——不要靠数源码里的括号来推断分组编号。
3. **gitleaks allowlist 的语义**：本 demo 只实现 path / regex / stopword 三类与
   `regexTarget`，未实现 commit / target rule 级别的 allowlist。

## 二、开发期修掉的问题

1. **`analyze_line` 返回的是集合不是列表**：A6 写成 `== []` 恒假。
   这类错误很隐蔽——`set() == []` 为 False，报错信息看起来像"判据写反"。
2. **H1/H2 初版用了含非 charset 字符的串去做管线级断言**，
   而 detect-secrets 的抽取正则要求候选串**完全由 charset 组成**，
   于是根本抽不出候选（空结果），断言测的是"抽不到"而不是"熵不够"。
   改为：函数级对比用含非 charset 字符的串，管线级对比用 charset-only 串。
3. **H2d 的正则漏了空格**：`x="([a-z]+)"` 匹配不了 `x = "abcd"`，
   改成 `x\s*=\s*"([a-z]+)"`。

## 三、容易写反的断言

- **A5 是「恒不大于」而不是「相等」**：对 200 个随机串断言
  `ds <= gl + 1e-9`。只测一个串会漏掉"某些串反而更大"的实现错误。
- **B2（放过）与 B3b（上报）必须成对**：只测"长串上报"是伪断言，
  任何阈值都会让某个串上报；关键是**同一个数字集合、只改长度**结论就翻转。
- **C2/C3 的 filetype 分派**：必须同一行文本在两个 filetype 下各测一次，
  只测一边看不出"按文件类型换正则"这件事。
- **G3/G3b 的 `regexTarget`**：同一条 allowlist 正则、只改 target，
  结论必须相反；只测一边会让"target 没实现"的实现也全绿。
