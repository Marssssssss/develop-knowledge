# 435 差分火焰图：宽度取「之后」，颜色取「差值」

> 排查性能回归最难受的不是"哪里慢了"，而是"**比上次**哪里慢了"。差分火焰图把两张剖面合成一张：**形状和宽度用第二份 profile**（看当前长什么样），**颜色用 `2 − 1` 的 delta**（看是怎么变成这样的）——红=变多，蓝=变少。

## 1. 简介

本 demo 依据 Brendan Gregg 2014-11-09《Differential Flame Graphs》实现：

- `difffolded.pl` 的三列合并（`stack v1 v2`）；
- **宽度取 after、颜色取 delta** 的帧模型与红蓝饱和度映射；
- `-n` 归一化（消除整体负载差异）、`-x` 剥十六进制地址、`--negate` 反转；
- **elided 比例**（在 profile 1 里存在、profile 2 里消失的栈占比）；
- 与 Robert Mustacchi「只画差值」、Cor-Paul Bezemer「三视图」两种方案的宽度语义对比。

## 2. 原理详解

### 2.1 四步算法（原文原文）

1. 取 stack profile 1；
2. 取 stack profile 2；
3. **用 2 生成火焰图**——这一步决定所有帧的宽度；
4. **用 "2 − 1" 的 delta 上色**：2 里出现更多是**红**，更少是**蓝**，饱和度相对 delta。

> "The colors show the difference that function **directly** contributed (eg, being on-CPU), **not its children**."

这一条在折叠格式里天然成立：每个栈一行，帧 `a` 的自有贡献只看 `a` 这一行，不含 `a;b` / `a;c`。本 demo 用一组数据把它钉死：**帧 `a` 自身 delta = 5，而 `a` 整棵子树 delta = 45**——如果你按子树上色，每帧都会红得发紫，整张图就废了。

### 2.2 为什么必须 `-n`

不同时间采集的两份 profile，样本总量天然不同（负载不一样）。不归一化会得到「全红」或「全蓝」，毫无鉴别力。`-n` 把第一份的总量拉平到第二份：

| 场景 | 不归一化 | `-n` 之后 |
| --- | --- | --- |
| 代码未变、负载翻倍（`a`100→200、`b`100→200） | 所有帧 delta > 0，**全红** | 所有帧 delta = 0，**全白** |

缩放因子就是 `sum(p2) / sum(p1)`。

### 2.3 `-x`：符号化失败的坑

profilers 有时解析不出符号，会把裸十六进制地址写进栈。两次运行里 `foo+0x1a2b` 和 `foo+0x3c4d` 是**同一个函数**，但字符串不同 ⇒ 被当成两条栈 ⇒ 制造假差异。`-x` 把 `0x...` 整段剥掉后合并，剩下的才是真差异。

### 2.4 消失的代码路径与 `--negate`

这是差分火焰图最大的盲区：**在 profile 2 里彻底消失的代码路径没有东西可以涂蓝**——你只看到现在的形状，看不到"我们是怎么走到这儿的"。

原文给了两个解药：

1. **`--negate`**：把两份 profile 反过来画。宽度变成第一份（before）、颜色变成"**即将**发生什么"。与正向图并排看：
   - `diff2.svg`：宽度=after，颜色=**已经**发生了什么；
   - `diff1.svg`：宽度=before，颜色=**即将**发生什么。
2. **elided flame graph**：把消失的路径单独画一张，页面上只显示 "X% elided"，点进去才展开。默认不展示是因为大多数时候这个比例很小，画出来白白翻倍矩形数量、拖慢浏览器。

### 2.5 另外两种差分形态

| 方案 | 宽度语义 | 优点 | 缺点 |
| --- | --- | --- | --- |
| **Gregg 红/蓝** | 第二份 profile 的样本数 | 保留全局上下文，宽度仍是熟悉的 CPU 占比 | 消失路径看不见 |
| Robert Mustacchi | delta 本身 | 只显示变化 | 宽度失去"全局占比"含义，难读 |
| Cor-Paul Bezemer | 三视图（A、B、差值同屏） | 上下文最全 | 矩形数翻倍，复杂 profile 上很卡 |

同一套代码还能画 **CPI 火焰图**：差值不是两份 profile，而是 **CPU cycles 与 stall cycles**。

## 3. 生成流程（原文命令）

```bash
perf record -F 99 -a -g -- sleep 30
perf script > out.stacks1          # 改动/过一段时间后再采一份
perf script > out.stacks2
./stackcollapse-perf.pl out.stacks1 > out.folded1
./stackcollapse-perf.pl out.stacks2 > out.folded2
./difffolded.pl out.folded1 out.folded2 | ./flamegraph.pl > diff2.svg
```

## 4. 环境与运行方式

```bash
cd 11-性能分析/01-系统级剖析/差分火焰图
python diff_flame_check.py     # 35 条断言，全部实跑通过
go run diff_flame.go           # 需 Go 工具链（本机无，走人工审查 + 机械核查）
```

## 5. 关键代码

```python
@dataclass(frozen=True)
class DiffFrame:
    stack: str; before: float; after: float
    @property
    def delta(self): return self.after - self.before   # 颜色
    @property
    def width(self): return self.after                 # 宽度
    def hue(self, max_delta):
        r = self.delta / max_delta
        return f"red@{abs(r):.3f}" if r > 0 else f"blue@{abs(r):.3f}"
```

## 6. 性能边界

- 饱和度按**全图最大 |delta|** 归一化：一个巨大的回归会把所有小变化的颜色压成接近白色。
- `-n` 假设负载差异是**全局均匀**的；如果只有某个子系统负载变化，归一化会掩盖真实回归。
- `elided` 比例高时，单看 diff2.svg 会严重低估"消失掉的开销"。
- 折叠格式的栈字符串是**精确匹配**的，函数名里的模板参数、行号、地址都会造成假差异（`-x` 只解决地址这一类）。

## 7. 注意事项与常见坑

1. **别忘了 `-n`**——否则负载一变，整张图全红，你什么也定位不到。
2. **颜色不能累加**：父帧的颜色只代表它自己的 delta，不要对着父帧宽度解读子树变化。
3. **消失路径要另画**：只看 diff2 会漏掉"整条路径消失"这类最大的收益。
4. 两套 profile 的采样频率、采集时长、折叠工具版本要一致，否则 delta 里混进工具差异。
5. `--negate` 不是"取反颜色"这么简单，它同时换了**宽度来源**（变成 before）。
6. 用于非回归测试时建议**两张图一起出**（diff1 + diff2）。

## 8. 参考资料（已读）

- [Brendan Gregg — Differential Flame Graphs (2014-11-09)](https://www.brendangregg.com/blog/2014-11-09/differential-flame-graphs.html)——四步算法与 `2 − 1` 着色、"只反映自身贡献不含孩子"、三列输出格式与 `func_a;func_b;func_c 31 33` 样例、`-n` / `-x` / `--negate`、消失路径与 elided、Mustacchi 与 flamegraphdiff 两种替代方案、CPI 火焰图
- 同目录 [火焰图生成/](../火焰图生成/)（demo 093，folded → SVG 的宽度与配色）、[CPU利用率口径与IPC/](../CPU利用率口径与IPC/)（demo 432，CPI/停顿口径）
