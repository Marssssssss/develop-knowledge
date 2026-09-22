# 可复现构建：阻力清单与 SOURCE_DATE_EPOCH

## 一、简介

**可复现构建**的定义：给定同一份源码与同一个构建环境，两次构建产出**逐字节相同**的产物。
它的意义是给供应链加一道"第三方可验证"的闸门 —— 任何人都能自己重编一遍，
比对哈希，从而发现构建机被植入后门、或分发渠道被掉包。

难点在于：绝大多数工具链默认会把**构建时的环境信息**烙进产物。本 demo 把这几类阻力
逐个建模，并给出对应的归一化手段。

## 二、SOURCE_DATE_EPOCH（规范）

https://reproducible-builds.org/specs/source-date-epoch/ 的关键条款：

- **值的格式**：UNIX 时间戳（1970-01-01T00:00:00Z 起的秒数），必须是
  `date +%s` 那样的纯十进制整数；**畸形值构建进程应当以非零码退出**，而不是静默回退。
- **用途**：构建进程凡是需要"当前时间"，都必须用它代替墙上时钟。
- **时间戳钳制（clamping）**：对于**不是当前时间、但仍随这一次构建变化**的时间戳，
  必须取"不晚于 SOURCE_DATE_EPOCH"的值。
- **不得对子进程 unset**："Build processes MUST NOT unset this variable for child
  processes if it is already present."
- **值应当只依赖源码**：规范建议设为源码的最后修改时间（Debian 取自 `debian/changelog`
  的最新条目，也可用最新 git commit 时间）。

### 2.1 钳制是**上界**，不是下界

规范的原话是：

> One can reasonably assume that all source timestamps are before SOURCE_DATE_EPOCH and all
> builds take place after it. This means we can efficiently both preserve source-based
> timestamps and omit build-specific timestamps, by rewriting timestamps more recent than
> SOURCE_DATE_EPOCH back to the latter.

对应 GNU tar 的 `--clamp-mtime`。所以：

```
clamp(t) = t            if t <= SOURCE_DATE_EPOCH   # 源码自带的时间戳要保留
clamp(t) = SOURCE_DATE_EPOCH  if t >  SOURCE_DATE_EPOCH   # 构建产生的时间戳一律压平
```

写成下界钳制是常见错误 —— 那样会把源码里合法的旧时间戳也抬平。自检里用
「早于 SDE 的时间戳原样保留」和它的负控钉死这一点。

### 2.2 一个具体的下界：ZIP 存不下 1980 年以前

规范文档给 Python 用户的示例里有个常量：

```python
filetime = max(315532800, int(os.environ.get('SOURCE_DATE_EPOCH', time.time())))
```

`315532800` 就是 **1980-01-01 00:00:00 UTC** —— ZIP 格式的时间戳下界。
于是 ZIP 场景要**先抬下界再压上界**。自检里断言了这个常量的日期含义，
以及 `0 -> 315532800`、`SDE-1 -> SDE-1`、`SDE+1 -> SDE` 三条边界。

## 三、构建路径泄漏（Build path）

编译器会把源文件路径写进调试信息，而 `__FILE__` 宏（比如 `assert` 里）会把路径写进
字符串常量。换一个目录构建，产物就变了。Build path 页给的三件工具：

| 选项 | 处理 |
| --- | --- |
| `-fdebug-prefix-map=OLD=NEW` | 调试信息里的路径 |
| `-fmacro-prefix-map=OLD=NEW` | `__FILE__` 展开出来的路径 |
| `-ffile-prefix-map=OLD=NEW` | 上两者的别名，一次搞定 |

自检里有一条专门验证**文件名与文件内容里的路径都要重映射**（分别对应调试信息与 `__FILE__`），
并配一条"不指定 build_path 时原样保留"的对照组。

页面还给了一个反例：`debugedit` 是**原地改写字节**，不重排字符串哈希表，
所以产物字节仍然依赖原始构建路径 —— 也就是说"看起来一样"不等于"字节一样"。

## 四、顺序不稳定与区域设置

目录遍历顺序、并行构建的完成顺序都不稳定，产物里条目的排列会随之变化。
解法是**显式排序**；但排序本身又受区域设置影响：

```
LC_ALL=C           : B.txt  C.txt  _x.txt  a.txt      # 按字节，大写在前
LC_ALL=en_US.UTF-8 : a.txt  B.txt  C.txt   _x.txt     # 忽略大小写与标点
```

这就是构建环境要求 `LC_ALL=C` 的原因。本 demo 的归一化内部**按字节排序**，
因此无论外部区域设置如何都得到同一结果（自检有断言）。

> 模型口径：en_US.UTF-8 的排序键用"忽略大小写与非字母数字"近似 glibc collation 的第一遍，
> 只用于说明"区域设置会改变顺序"，不是完整实现。

## 五、时区、umask 与属主

- **TZ**：同一个时刻在不同时区可能格式化成不同日期。自检挑了一个跨日时刻（UTC 当天 23:30）
  展示 UTC 与 UTC+2 得出不同日期。
- **属主 / 属组**：打包时 uid/gid/uname/gname 必须清零，否则谁编的就露馅了。
  归一化管线里一并处理。
- **umask**：这个**归一化管不了**。mode 是产物的一部分，改 umask 就是改产物。
  自检里明确断言「只改 umask 时归一化后**仍然**不同」，并配了「把 umask 固定回 022 后一致」
  的对照组 —— 只能靠**约束构建环境**（容器/固定 umask）解决。

## 六、无法让工具支持时的后处理

Timestamps 页给了两条退路，也各带一个坑：

- **`strip-nondeterminism`**：对产物做后处理，抹掉或归一时间戳。
- **`libfaketime`**：用 `LD_PRELOAD` 拦截取时间的调用，返回固定时间。
  坑是**如果构建过程依赖时间差**（比如并行编译的超时判断），它会出问题 ——
  文档点名了 Tor Browser 因此产生可复现性问题。

## 七、代码结构与验证

| 文件 | 内容 |
| --- | --- |
| `python/repro.py` | SDE 解析、钳制、ZIP 下界、区域排序、归一化管线、构建摘要 |
| `python/selfcheck_repro.py` | 44 条断言 |
| `python/main.py` | 演示 |
| `go/repro.go` `go/main.go` | 同构 Go 实现（无 Go 工具链，走人工审查 + 三项静态检查） |

验证方式：同一份源码在两个"环境"（mtime / 路径 / umask / uid 全不同）下构建 ——
不归一化时摘要不同，归一化（且 umask 统一）后摘要相同；再逐项拆开验证每个阻力。

## 八、参考资料（本轮实际读取）

- SOURCE_DATE_EPOCH 规范：https://reproducible-builds.org/specs/source-date-epoch/
  （值的格式、钳制、不得 unset、畸形值处理、各语言读取示例）
- 使用说明页：https://reproducible-builds.org/docs/source-date-epoch/
- Timestamps 页：https://reproducible-builds.org/docs/timestamps/
  （strip-nondeterminism 与 libfaketime 的取舍与坑）
- Build path 页：https://reproducible-builds.org/docs/build-path/
  （`-fdebug-prefix-map` / `-fmacro-prefix-map` / `-ffile-prefix-map`、`debugedit` 的反例）
- 文档索引：https://reproducible-builds.org/docs/
