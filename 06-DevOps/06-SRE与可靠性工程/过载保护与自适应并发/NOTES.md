# 注意事项完整版

README 的「注意事项与常见坑」只留了 5 条摘要,这里给出每条的
**现象 → 原因 → 规避方法**,以及开发期实测到的具体数字。

## 1. buffer 的量纲:百分数还是小数

- **现象**:梯度恒大于 1,并发上限指数增长(实测第 12 步已到 262,再往后是天文数字)。
- **原因**:文档写 `B = minRTT * buffer_pct`,同时又说 "The buffer will be a
  percentage of the measured minRTT value"。配置里填的 10 表示 10%,若按字面把它当
  0.10 用在乘法里,B 会变成 `minRTT × 10 = 500ms`,分子比分母大十倍。
- **规避**:实现里显式 `/100`,并在注释标注该口径。判别式是
  `sampleRTT = minRTT × (1 + buffer/100)` 时梯度**恰好等于 1**(本例 55ms),
  用这条做单元测试即可锁住量纲。

## 2. 稳态只在 g < 1 时存在

- **现象**:`g ≥ 1` 时 `limit` 单调发散,闭式 `1/(1−g)²` 无定义。
- **原因**:`headroom = sqrt(limit)` 恒为正,`g ≥ 1` 时每步都在做加法且乘数不小于 1。
- **规避**:不要用"迭代 N 步看它稳不稳"来判断配置好坏 —— 应当直接用闭式
  `L = 1/(1−g)²`(g<1)判断;g≥1 时说明上游延迟低于 buffer 允许的范围,应当
  调小 buffer 而不是调小 min_limit。

## 3. minRTT 测量期必然产生 503

- **现象**:周期性出现一批 503。
- **原因**:测量 minRTT 时把并发钉在 `min_concurrency`(**缺省 3**),容量骤降。
  文档原文即承认: "It is possible that there is a noticeable increase in request
  503s during the minRTT measurement window".
- **规避**:对 503 / reset 开启重试,并用 `previous_hosts` 这类重试谓词换一个 host;
  文档理由是 "Due to the minRTT recalculation jitter, it's unlikely that all hosts
  in the cluster will be in a minRTT calculation window"。
  也可调大 `min_concurrency_limit` 减小掉容量幅度。

## 4. jitter 设成 0 会让集群同步掉容量

- **现象**:整个集群同时进入测量窗口、同时把并发压到 3,形成周期性整体掉容量。
- **原因**:jitter 的作用就是"随机推迟测量窗口起点"。实测 20 个 host、容差 ±5%:
  jitter=0 → 20 个全对齐;jitter=10% → 12 个;jitter=50% → 5 个。
- **规避**:jitter 至少给到 10%;分布文档未规定,本 demo 取均匀分布(口径已标注)。

## 5. headroom 导致过冲

- **现象**:收敛轨迹不是单调的。实测 137.29 → **140.4746(峰值)** → 140.11 → 140.149。
- **原因**:`headroom` 恒为正,即使 `g < 1` 也会先把上限顶过稳态再回落。
- **规避**:不要用"单调爬升"或"精确等于闭式稳态"做断言;闭式只能用于**收敛值**
  的量级核对,收敛过程要留容差。

## 6. cgroup 内存压力:没配 limit 就是 0

- **现象**:基于内存的降载动作永远不触发。
- **原因**:文档原文 "When no memory limit is set in cgroup (indicated by -1 in v1 or
  'max' in v2), the pressure is reported as 0."
- **规避**:部署时显式设置 cgroup 内存上限;用 `memory_pressure(usage, limit)` 对
  `limit ∈ {None, 0, -1}` 三种"无限制"写法都返回 0 的用例做回归。

## 7. threshold 触发器是严格大于

- **现象**:压力恰好等于阈值时不触发,看起来"阈值配错了"。
- **原因**:文档原文 "Sets the action state to 1 (= saturated) when the resource
  pressure is **above** a threshold, and to 0 otherwise."
- **规避**:边界测试要取 `threshold ± ε`,不要取等号;若希望"到达即触发",改用
  `scaled` 并把 `scaling_threshold` 设为该值。

## 8. 口径标注汇总(文档未明确、本 demo 自行选择的地方)

| 位置 | 选择 | 理由 |
| --- | --- | --- |
| `B` 的量纲 | `minRTT × buffer_pct / 100` | 配置字段语义是 percent |
| `headroom` 取新值还是旧值 | **旧值**(更新前) | 迭代式实现的惯例 |
| `jitter` 的随机分布 | 均匀分布 | 文档只说 "randomly delay" |
| minRTT 触发后连击是否清零 | **清零** | 否则会后连续触发 |
