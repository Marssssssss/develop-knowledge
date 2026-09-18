# Alertmanager 分组、抑制与静默

纯手写实现 Alertmanager 的**匹配与路由语义**(matcher 语言、路由树遍历、`continue`
阻断、分组键、抑制规则、静默窗口)与**通知节奏**(group_wait / group_interval /
repeat_interval),以及 Prometheus 侧的**告警状态机**(`for` 的 pending→firing、
`keep_firing_for` 防抖)。

不引入第三方依赖也不联网。Python 版**实跑断言**(109 条全绿);Go 版因本机无工具链
只做机械核查。

## 原理详解

### 1. 职责分界:Prometheus 管状态,Alertmanager 管通知

```
Prometheus                               Alertmanager
  expr ──> pending ──for──> firing ──POST /api/v2/alerts──> 分组 ─> 路由
                                                             └── 抑制 / 静默
  合成序列 ALERTS{alertname,alertstate} / ALERTS_FOR_STATE(重启恢复 for 计时)
```

以「现在是不是坏了」为界:Prometheus 只管前者,**分组、抑制、静默、发给谁、发多频**
全在 Alertmanager。所以「发错人」的 bug 几乎从不在规则文件里。

### 2. matcher 语言:没有 IN 操作符

```
key="value"   等值      key!="value"  不等
key=~"regex"  正则(完全锚定)   key!~"regex"  正则取反
```

三个必须记住的语义:

- **没有 `IN`**:多值只能写 `service=~"mysql|cassandra"`。写成
  `service=~"mysql,cassandra"` 一个都匹配不上,而且配置本身不报错。
- **`=~` 完全锚定**:`team="payments"` **不会**匹配 `team="payments-eu"`。
- **缺失标签等价于空字符串**:`env="prod"` 对没有 `env` 的告警不命中,而
  `env!="prod"` 反而**命中**,`env=~".*"` 也命中。

### 3. 路由树:命中即停,`continue` 才继续

```
root(default-receiver, group_by=[alertname, cluster])
├── db    service=~"mysql|cassandra"   group_wait=10s
├── fe    team="frontend"              group_by=[product, environment]
├── crit  severity="critical"          continue: true
├── pay   team="payments"
└── plat  team="platform"
```

遍历规则(官方 `Match` 语义):本节点 matchers 不命中 → 整棵子树出局;依次尝试子节点,
**某个子节点命中后若它不是 `continue: true`,同层后续节点不再尝试**;没有任何子节点
贡献结果 → 由本节点的 receiver 兜底。

`continue: true` 是「一条告警同时进多个 receiver」的唯一手段(默认 false)。把它误删,
表现是「不再重复打扰」,但另一路静默失联,极难发现。

**子节点未显式设置的参数继承父节点**:上例 `fe` 没写 `group_wait`,拿到 root 的 30s;
`db` 显式写了 10s 就用自己的。

### 4. 分组与三个时间参数

| `group_by` | 语义 |
| --- | --- |
| 空列表(默认) | **所有告警聚成一个组** |
| `['...']` | 不聚合,每条告警独占一组 |
| `[a, b]` | 按 a、b 的取值组合 |

```
新 group:    首见 ──group_wait(30s)──> 首条通知
已有 group:  每 group_interval(5m) 检查一次
               ├─ 有新告警 firing 或有告警 resolved → 发(changed)
               └─ 无变化 → 距上次发送是否已过 repeat_interval(4h)
```

官方注释里两条容易忽略的细节:

- **告警若在 `group_wait` 结束前就已 resolved,则不发通知** —— 这就是它天然抑制抖动的
  原理;错过初始 `group_wait` 的告警会在下一个 `group_interval` 补发。
- **`repeat_interval` 应为 `group_interval` 的倍数,否则向上取整**到下一个倍数。

### 5. 抑制与静默

```
inhibit_rules:
  source_matchers: severity="critical"
  target_matchers: severity="warning"
  equal: [alertname, cluster, service]
```

「source 命中 且 与 target 在 `equal` 标签上全部相等 → target 的通知被抑制」。`equal` 的
语义是**两边都缺该标签也算相等**(缺失 ≡ 空值)—— 这是最容易配错的一处:把 `instance`
写进 `equal` 后,没有 `instance` 标签的聚合告警之间会**互相抑制**。`equal` 留空则退化成
「只要源告警在跑,所有 target 匹配到的告警都被抑制」。

**静默**与之的区别:抑制写在配置里、长期有效;静默通过 UI/API 动态创建,是一组 matchers
加一个时间窗口,窗口内所有 matcher 命中的告警都不通知。窗口是**左闭右开**的。

### 6. 告警状态机

```
inactive ──expr 命中──> pending ──连续满足 for──> firing ──expr 不再命中──> inactive
             ^              │                        │
             │              └─ 任一评估不命中即归零   └─ keep_firing_for 窗口内保持 firing
```

- `for` 期间处于 **pending**,只有 firing 才会发给 Alertmanager。
- **任一评估为 false 就重置 `for` 计时器** —— 抖动型指标会让告警永远停在 pending。
- `keep_firing_for`(默认 **0**)让条件不再满足后仍保持 firing 一段时间,用于防抖与
  「数据缺失导致的假 resolved」。
- 合成序列 `ALERTS{alertname, alertstate}` 活跃期间恒为 1,不再活跃即 stale;
  `ALERTS_FOR_STATE` 携带告警转 active 的时刻,重启后靠它恢复 in-flight 的 `for` 计时。

## 对比

| 手段 | 定义位置 | 生效期 | 典型用途 |
| --- | --- | --- | --- |
| inhibition | 配置文件 | 长期 | 根因已告警时压掉派生告警 |
| silence | UI/API 动态 | 指定窗口 | 维护窗口、已知故障 |
| `mute_time_intervals` | 配置文件 | 按时间表 | 值班表外不打搅 |

| 状态 | 发给 Alertmanager | 产生通知 |
| --- | --- | --- |
| inactive / pending | 否 | 否 |
| firing | 是 | 视分组、抑制、静默而定 |

## 环境准备

Python 3.8+(本 demo 用 3.13 实跑,仅标准库);Go 1.21+ 为可选项。无需网络与第三方包。

## 运行方式

```bash
cd python && python demo.py     # ALL PASS  109 assertions(已实跑)
cd go && go run .               # 预期 109 条,本机无 go 工具链未实跑
python _docs/tools/go_sanity.py go/*.go                    # 未用 import / 参个数 / 重名
python _docs/tools/bracket_check.py go/*.go python/*.py    # 括号平衡
```

## 关键代码片段

`continue` 的阻断语义是路由树里最容易漏的一行:

```python
for child in route.routes:
    if not labels_match(child.matchers, labels):
        continue
    out.extend(dispatch(child, labels, eff, path + (child.name,)))
    if not child.continue_:
        break        # 命中即停:同层后续 route 不再尝试
```

通知节奏完全由三个参数 + 「内容是否变化」决定:

```python
def decide(self, group, now, current_fps):
    fps = set(current_fps)
    if group.last_sent is None:
        if not fps:
            return None                      # group_wait 内全部 resolved:不发
        return "first" if now >= group.first_seen + self.group_wait else None
    if now - group.last_sent < self.group_interval:
        return None
    if fps != set(group.last_fps):
        return "changed"                     # 有新告警 / 有告警 resolved
    return "repeat" if now - group.last_sent >= self.effective_repeat else None
```

## 性能与边界

- **`for` 应是 `evaluation_interval` 的小倍数**:interval 30s、`for: 2m` 意味着需要 4 次
  连续命中;只留 1–2 次评估的 `for` 非常脆弱。
- **`group_interval` 同时是通知管道的 context timeout**:发送耗时超过它,这次通知会被
  取消 —— 小 `group_interval` 配慢接收方会持续丢通知。
- **`repeat_interval` 长于 `--data.retention` 时会被截断**,官方注释说明此时改为在数据
  保留期结束时重发。
- 通知队列默认容量 10000,队满丢弃并计入 `prometheus_notifications_dropped_total`。

## 注意事项与常见坑

1. **matcher 没有 `IN`**:多值必须写 `service=~"mysql|cassandra"`,逗号分隔会静默失配。
2. **`=~` 完全锚定**:`team="payments"` 不匹配 `team="payments-eu"`,多值同理必须加锚点。
3. **缺失标签 ≡ 空值**:`env!="prod"` 对没有 `env` 的告警**命中**,写排除规则时极易误伤。
4. **`equal` 里两边都缺该标签算相等**:无 `instance` 的聚合告警会互相抑制。
5. **`continue` 默认 false**:同时通知两路必须显式 `continue: true`,否则一路静默失联。
6. **`group_by` 留空是「全部聚成一组」而非「不聚合」**:高基数告警会把整组撑爆,通知被
   合并;要逐条通知得写 `['...']`。
7. **root 节点不能带 matchers**:带了就不再对所有告警生效,未命中者无处可去。
8. **`keep_firing_for` 太长会掩盖真实恢复**,排查时先看它再看指标。
9. **抖动指标让 `for` 计时器不断归零**,告警永远停在 pending —— 用 `ALERTS_FOR_STATE`
   量真实持续时长(`time() - ALERTS_FOR_STATE{...}`),再决定调小 `for` 还是平滑表达式。
10. **`group_wait` 内 resolved 不发通知是特性不是 bug**,别为了「看到所有告警」把它调 0。

## 参考资料

- Prometheus 官方 [Alertmanager Configuration](https://prometheus.io/docs/alerting/configuration/) —— route/group_wait/group_interval/repeat_interval 默认值与注释、子 route 继承、mute/active time intervals
- Prometheus 官方 [Alerting rules](https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules) —— `for`、`keep_firing_for`、pending 语义、`ALERTS{alertname, alertstate}` 合成序列
- runbook.academy [Rules and the Alerting Pipeline](https://runbook.academy/courses/observability/lessons/04-rules-and-alerting-pipeline) —— 状态机图、`ALERTS_FOR_STATE` 恢复、notifier 队列容量与丢弃指标
- runbook.academy [for: and Hysteresis](https://runbook.academy/courses/observability/lessons/03-for-and-hysteresis) —— `for` 与评估间隔的关系、六种失效模式
- runbook.academy [Alertmanager Config as Code](https://runbook.academy/courses/observability/lessons/04-alertmanager-as-code) —— matcher 无 IN 操作符、锚定正则、抑制规则写法
- DeepWiki [yunlzheng/prometheus-book: Alertmanager Configuration](https://deepwiki.com/yunlzheng/prometheus-book/4.2-alertmanager-configuration) —— route 参数默认值表、抑制与静默的定位差异
- devopsaitoolkit [Alerts Stuck Pending and Never Firing](https://devopsaitoolkit.com/blog/prometheus-error-alerts-stuck-pending-not-firing) —— 用 `time() - ALERTS_FOR_STATE` 量真实持续时长

> 不同来源对 API 字段 `keepFiringSince` 的口径不一致(一处称"转为 firing 的时刻",
> 另一处称"条件最后一次满足的时刻")。本 demo 只断言"条件不再满足后保持 firing 达
> `keep_firing_for` 时长"这一有官方文档支撑的行为,不对该字段下结论。
> 指纹实现用 SHA-256 替代真实实现的 xxhash,只保证稳定性与区分度。
