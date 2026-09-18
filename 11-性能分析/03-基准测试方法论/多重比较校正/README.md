# 多重比较校正:FWER、FDR 与"family"的定义

> 一次 `benchstat old.txt new.txt` 里如果有 50 个 benchmark,那么**在没有任差异的前提下**,平均会有 2.5 个报"显著",而且**至少有一个**的概率高达 92%。这不是 bug,是 α=0.05 的定义。

## 1. 官方把话说得很直白

benchstat 的 Tips 节原文:

> By default, benchstat uses an ɑ threshold of 0.05, which means it is *expected* to show a difference 5% of the time even if there is no difference. Hence, if you rerun benchmarks looking for a change, benchstat will probably eventually say there is a change, even if there isn't, which creates a statistical bias.
>
> As an extension of this, if you compare a large number of benchmarks, you should expect that **about 5% of them** will report a statistically significant change even if there is no difference between the before and after.

自检 `[5]` 直接把这句话跑了出来:50 个无差异 benchmark、3000 次实验,平均报出 **2.52** 个"显著"(期望 2.5)。自检 `[9]` 验证它随 m 线性增长:m=10/50/200 时分别约 0.5 / 2.5 / 10 个。

**关键:benchstat 自己不做任何校正** —— 文档里没有一个 Bonferroni 或 BH 字样,它只负责警告。所以"family 是什么"完全由使用者决定。

## 2. 两个错误率不是一回事

| | 定义 | 控制它的常用方法 |
| --- | --- | --- |
| **FWER** | `P(V ≥ 1)`,family 里出现**至少一个**假阳性的概率 | Bonferroni、Šidák、Holm、Hochberg |
| **FDR** | `E[V / max(R,1)]`,被拒的集合里假阳性的**期望比例** | Benjamini-Hochberg、Benjamini-Yekutieli |

`max(R,1)` 的兜底不是笔误:全都不拒时 `V/R` 是 0/0,定义上必须兜住。

临界常数(原始论文):

- **BH (1995)**,JRSS-B 57:289–300:step-up,临界常数 `α_i = (i/m)α`,独立下 `FDR ≤ (m₀/m)α`。
- **BY (2001)**,Ann. Statist. 29:1165–1188:同一过程在 **PRDS**(正回归依赖)下仍成立;任意依赖下必须改为 `α_i = iα / (m·Σ_{j=1}^{m} 1/j)`。

statsmodels `multipletests` 文档对适用范围的原文:

> All procedures that are included, control FWER or FDR in the independent case, and most are robust in the positively correlated case.

## 3. 最锋利的一条:全局零效应下 FDR **等于** FWER

如果所有假设都是零效应(`m₀ = m`),那么每个被拒的都是假阳性,`V ≡ R`,于是 `V/R = 1`(只要 R>0),`FDR = P(R>0) = FWER`。

自检 `[4]` 实测:m=50 全零效应,未校正 FWER = **0.929**,FDR 也是 **0.929**(理论 `1-0.95^50 = 0.923`)。

推论:**当你的改动其实什么都没影响时,BH 并不比 Bonferroni 松**(实测 BH 的 FWER 也只有 0.049)。BH 的优势只在**有真效应**时才出现。

## 4. 有真效应时,两者才分道扬镳

自检 `[6]`,m=50 其中 10 个真效应(d=1.0、每侧 n=16):

| 方法 | FDR | FWER | 功效 |
| --- | --- | --- | --- |
| BH | **0.039** | 0.220 | **0.530** |
| Bonferroni | — | **0.033** | 0.322 |
| BY | ≤ BH | — | ≤ BH |

这就是取舍的定量版本:BH 把"至少有一个假阳性"的概率从 3.3% 抬到 22%,换来功效从 32% 涨到 53%。

- **要"绝不能误报"**(比如自动回滚流水线)→ 用 FWER 方法。
- **要"不放过真回归"**(比如每周人工过一遍的告警列表)→ 用 FDR 方法。

## 5. 该选哪个:几条可操作的判据

1. **别再用裸 Bonferroni**。Holm 是 step-down 的 Bonferroni,同样控制 FWER,且**恒为 Bonferroni 拒绝集的超集**。自检 `[3]` 用 300 组随机 p 向量逐一验证了包含关系。**没有任何理由优先选 Bonferroni**。
2. **Šidák 略宽于 Bonferroni**(`α_c = 1-(1-α)^{1/m}`),m=50 时 0.0010248 vs 0.0010000——差别很小,但同样没有理由不选 Holm。
3. **BH 的阈值是自适应的**。自检 `[7]`:10 个真效应时 BH 的最大被拒原始 p 是 **0.00534**,而 Bonferroni 的判据恒为 `p ≤ 0.00100`。真效应越多,BH 越宽松(因为它估计出的 `m₀` 越小)。
4. **基准场景通常是正相关的**(同一台机器、同一个 CPU 频率、同一段时间的温度),符合 PRDS,BH 适用。但如果 bench **并行**跑、互相抢资源,可能出现负相关,此时 BH 的 FDR 保证失效,应退到 BY。
5. **BY 的代价是 `H_m` 倍**。m=50 时 `H_50 = 4.4992 ≈ ln50 + γ`,自检 `[8]` 断言 `BY校正p = BH校正p × H_m`。

## 6. 运行与自检

```bash
cd python && python multitest.py                        # 9 组断言,全部实跑通过
cd ../go    && go run multitest_core.go multitest_family.go
```

Go 版自带 splitmix64 + Box-Muller,**不依赖 `math/rand`** —— `math/rand` 默认发生器的序列不保证跨 Go 版本一致,而本 demo 的断言依赖可复现的模拟。

## 7. 注意事项与常见坑

1. **先定义 family 再谈校正**。"这次 PR 触及的 3 个 benchmark"和"整个仓库 300 个 benchmark"是两个完全不同的 family,校正强度差 100 倍。折中做法是按**受影响的包**分组。
2. **重跑到出现显著为止是另一种多重比较**。benchstat 文档把它单独点名了:这是"时间维度上的 family",校正方法管不了它,只能靠"事先定好次数并坚持"。
3. **BH 要求 p 值独立或 PRDS**。statsmodels 文档明确说"most are robust in the positively correlated case"——是"多数",不是"全部"。
4. **校正后的 p 值不是"真 p 值"**。它只是给定一个固定阈值下的等价判据,`p_adjusted ≤ α` 与原始判据等价,不要拿它去做别的事。
5. **FDR 不控制第 I 类错误的绝对数量**。m=200 时 FDR=5% 意味着平均 10 个假阳性——如果你的流程每收到一条告警就要人工排查一小时,这个成本要先算清楚。

## 参考资料(实际阅读过的来源)

- [`pkg.go.dev/golang.org/x/perf/cmd/benchstat`(Tips 节)](https://pkg.go.dev/golang.org/x/perf/cmd/benchstat) — "it is *expected* to show a difference 5% of the time"、"about 5% of them will report a statistically significant change"、重跑到显著即 "multiple testing" 的警告、"Reducing noise and/or increasing the number of benchmark runs will enable benchstat to discern smaller changes"
- [`statsmodels.stats.multitest.multipletests` 文档](https://www.statsmodels.org/stable/generated/statsmodels.stats.multitest.multipletests.html) — FWER/FDR 两类方法清单、step-up/step-down 分类、"All procedures ... control FWER or FDR in the independent case, and most are robust in the positively correlated case"
- [Benjamini & Hochberg (1995), *Controlling the False Discovery Rate*, JRSS-B 57:289–300](https://www.sciencedirect.com/science/article/abs/pii/S0378375808000165) — 经该文摘要确认:`α_i = (i/m)α`、独立下 `FDR ≤ (m₀/m)α`
- [Benjamini & Yekutieli (2001), *The control of the false discovery rate in multiple testing under dependency*, Ann. Statist. 29:1165–1188](https://digitalcommons.njit.edu/cgi/viewcontent.cgi?article=1174&context=dissertations) — 经该文献综述确认:PRDS 的定义、任意依赖下 `α_i = iα / (m Σ 1/j)`
- [Holm (1979) step-down 与 FWER 控制](https://theorempath.com/topics/statistical-significance-and-multiple-comparisons) — `p_(i) ≤ α/(m-i+1)`、停在第一处失败、"always rejects a (weak) superset of what Bonferroni rejects"
