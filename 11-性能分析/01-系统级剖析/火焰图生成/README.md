# 火焰图生成(FlameGraph)

## 简介

火焰图是 Brendan Gregg 于 2011 年创建的**层级堆栈可视化**:把成千上万条采样调用栈合并成一棵宽随占比、高随深度的矩形树,让最热的代码路径(最宽的"塔")一眼可见。它解决的问题是:profiler 输出动辄数千行文本,`perf report` 树状文本读几屏也只覆盖个位数百分比的样本,而火焰图把全部样本的占比压缩进一张图。

- **folded 格式**:火焰图工具链的中间格式,每行一条栈、帧用 `;` 分隔、行尾空格 + 计数,如 `swapper;start_kernel;cpu_idle;native_safe_halt 1`;单行文本便于 `grep` 过滤。
- **宽 = 占比**:帧的宽度正比于它出现在样本中的次数(含作为祖先的次数);**平 = 栈深**。
- **x 轴不是时间**:按帧名字母排序以最大化合并,时间信息已因采样而丢失(这正是 Gregg 放弃时间序可视化的原因)。
- **交互**:悬停显示详情、点击缩放、Ctrl-F 搜索并计算累计百分比(官方 flamegraph.pl 内嵌 JS)。
- **历史**:起源于一个 MySQL 性能问题——函数追踪开销太大且太密,Gregg 改用采样并把样本按字母排序合并;因暖色表示 CPU "热",得名 flame graph。

## 原理详解

生成三步(Gregg 官方流程):**采集栈 → 折叠 → flamegraph.pl**。

1. **采集**:用采样剖析器(见同目录 `采样剖析原理/`)拿 folded 栈;`perf record -F 99 -a -g` 后 `perf script | stackcollapse-perf.pl`。
2. **折叠**:多行原始栈变成单行 `frame;frame;... count`。
3. **渲染**:本 demo 的 Python 版完整实现 `flamegraph.pl` 的核心渲染逻辑:

折叠样本 → 前缀树合并的等价操作:

```text
"main;a;b 5"      )       main [width=9]
"main;a;c 2"      )  ==>   ├─ a [9] ── b [5]
"main;d 2"        )        │        └─ c [2]
                            └─ d [2]
```

渲染算法(flamegraph.pl 的关键公式,本 demo 逐条复现):

- `widthpertime = (imagewidth - 2*xpad) / timemax`,`timemax` = 全部计数之和;
- 帧矩形:`x1 = xpad + stime * widthpertime`,`x2 = xpad + etime * widthpertime`;
- **剪枝**:宽度小于 `minwidth`(默认 0.1px)的帧直接丢弃,同时得出 `depthmax`;
- 画布高:`(depthmax + 1) * frameheight + ypad1 + ypad2`;
- **x 排序**:`sort @Data` 按帧名字母序排列(默认),`--flamechart` 才按时间逆序不合并;
- **颜色**:暖色 `r = 205+50v3, g = 230v1, b = 55v2`;默认用"名字校验和作种子"的伪随机——同名函数同色且跨图一致;`--hash` 用前部字符权重更高的向量哈希;`--random` 纯随机;
- **转义**:`&` → `&amp;`、`<` → `&lt;`、`>` → `&gt;`、`"` → `&quot;`,且 `&` 必须最先替换,否则二次转义;
- 根帧强制占满 `timemax`,info 显示 `all (N samples, 100%)`;
- 帧内文字:按宽度算可容纳字符数,至少 3 字符才绘制,超长截断加 `..`;
- `-`(用户/内核栈分隔)用深灰,`--`(waker 链分隔)另有专色。

## 对比 / 选型

| 可视化 | x 轴 | 合并 | 适用 |
| --- | --- | --- | --- |
| Flame graph(本 demo) | 帧名字母序 | 最大化合并 | 看整体热点占比 |
| Flame chart(Chrome DevTools) | 时间 | 不合并 | 看时间模式/单线程 |
| Icicle graph | 同火焰图 | 同 | y 轴翻转,根在顶,深栈免滚动 |
| Sunburst | 径向 | 同 | 审美偏好,辨识度略低 |

## 环境准备

- Python ≥ 3.8(仅标准库);Go ≥ 1.18;两实现互相独立

## 运行方式

### Python(生成 SVG,兼容官方工具链)
```bash
python3 python/main.py            # 用内置样本生成 flamegraph.svg(当前目录)
cat out.perf-folded | python3 python/main.py - > my.svg   # 读标准输入
```

### Go(折叠解析 + 文本火焰图)
```bash
cd go && go run main.go          # 解析 ../python 样本,终端渲染 ASCII 火焰图
```

## 关键代码片段

Python 版渲染核心(对应"原理详解"公式):

```python
timemax = sum(counts.values())
widthpertime = (imagewidth - 2 * xpad) / timemax
# 每帧:遍历排序后的折叠栈,把计数切分为子区间
x1 = xpad + stime * widthpertime
x2 = xpad + etime * widthpertime
w = x2 - x1
if w < minwidth:      # 剪枝:小于 0.1px 的帧丢弃
    continue
fill = warm_color(name)  # 名字校验和作种子的伪随机暖色
```

Go 版把折叠栈构建成前缀树再逐层打印,验证"宽 ∝ 计数、层级 = 深度"的结构语义。

## 性能与边界

- 输入规模:官方工具处理百万级样本无压力;folded 单行文本可 `grep` 预过滤(`grep ext4 out.folded | flamegraph.pl`)。
- **采样数可超过流逝时间**:多线程并行被同时采样(Gregg 原文明确指出),30 秒追踪里出现 >30s 的塔是正常现象。
- 深栈 + LBR 采样的栈只有 8/16/32 帧,回溯不到公共根就**无法合并**,不适合火焰图(Gregg 对三种栈采集方式的结论)。

## 注意事项与常见坑

- **误读一:左右顺序有意义** —— 没有,是字母序;时间模式要看 flame chart。
- **误读二:塔顶窄 ≠ 快** —— 顶上的是叶子,宽只说明占比;调用次数采样不可知。
- **误读三:两个宽塔并排 = 并发** —— 只是排序相邻,不代表时间关系。
- 空闲线程会淹没信号:采样内核时先 `grep -v cpu_idle`(Gregg 的 DTrace 章节实践)。
- SVG 注入:函数名必须转义;`--notes` 含 `<`/`>` 时官方脚本直接 die。
- 折叠格式解析失败行会被跳过并在末尾警告(Ignored N lines)——遇到空图先查输入格式。

## 参考资料(实际阅读过的权威来源)

- [Flame Graphs — Brendan Gregg(官网)](https://www.brendangregg.com/flamegraphs.html) — 图形语义(x/y 轴、宽平读法)、六种变体、起源故事
- [CPU Flame Graphs — Brendan Gregg](https://www.brendangregg.com/FlameGraphs/cpuflamegraphs.html) — 采样与折叠流程、perf/DTrace 采集命令、99Hz 理由、读图细则
- [flamegraph.pl 源码 — github.com/brendangregg/FlameGraph](https://raw.githubusercontent.com/brendangregg/FlameGraph/master/flamegraph.pl) — folded 解析正则、宽度公式、暖色生成、转义顺序、参数全集(本 demo 逐条对照实现)
