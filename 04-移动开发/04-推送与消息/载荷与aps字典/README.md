# 载荷与 aps 字典

> APNs 远程通知的载荷是一个 JSON 字典，其中 `aps` 是 Apple 保留的子字典。**自定义键必须放在 `aps` 之外** —— 放进 `aps` 的自定义键会被**静默忽略**，既不生效也不报错。这是推送接调阶段最常见、也最难自查的一类问题。

来源：Apple 官方《Generating a remote notification》全文实读（Table 1 / Table 2 / Table 3）。

## 一、载荷的基本形状

```json
{
   "aps" : {
      "alert" : { "title" : "Game Request", "subtitle" : "Five Card Draw",
                  "body" : "Bob wants to play poker" },
      "category" : "GAME_INVITATION"
   },
   "gameID" : "12345678"
}
```

- `aps` 里放**系统行为**（alert / sound / badge / 静默 / 可变 / 实时活动等）；
- `aps` 的同级放**应用数据**，值必须是 primitive：dictionary / array / string / number / Boolean；
- 自定义键会出现在 `UNNotificationContent` 的 `userInfo` 里。

## 二、aps 键表（Table 1，按文档顺序）

| 键 | 类型 | 语义 |
| --- | --- | --- |
| `alert` | Dictionary（推荐）或 String | String 时整体作为 body |
| `badge` | Number | 角标数；**`0` 表示清除** |
| `sound` | String 或 Dictionary | 普通通知用 String（`"default"` 播系统音）；**critical alert 用 Dictionary** |
| `thread-id` | String | 通知分组 |
| `category` | String | 对应已注册的 `UNNotificationCategory`，决定操作按钮 |
| `content-available` | Number | **静默推送标志，只能为 `1`** |
| `mutable-content` | Number | **通知服务扩展标志，只能为 `1`** |
| `target-content-id` | String | 拉起指定窗口 |
| `interruption-level` | String | `passive` / `active` / `time-sensitive` / `critical` |
| `relevance-score` | Number | `0.0` ~ `1.0`，排序用；**Live Activity 可为任意 Double** |
| `filter-criteria` | String | 当前 Focus 下是否展示的判据 |
| `stale-date` / `content-state` / `timestamp` / `event` / `dismissal-date` / `attributes-type` / `attributes` | 混合 | Live Activity 专用 |

> `sound` 字典（Table 3）用于 critical alert：`critical`（1 启用）、`name`（`"default"` 播系统音）、`volume`（`0.0`~`1.0`）。

## 三、alert 字典与本地化（Table 2）

键：`title` / `subtitle` / `body` / `launch-image`，以及本地化用的 `loc-key` / `loc-args`、`title-loc-key` / `title-loc-args`、`subtitle-loc-key` / `subtitle-loc-args`。

本地化有**两条路**，取舍很明确：

1. **服务端直接下发本地化好的字符串** —— 灵活，但服务端必须知道用户的语言偏好；
2. **App bundle 里放 `Localizable.strings`**，载荷只给 key —— 系统按用户语言挑串。

第二种的替换规则是文档里最容易被实现错的一条：

> "Each `%@` character in the string specified by `<key>` is replaced by a value from this array. **The first item in the array replaces the first instance** of the `%@` character, **the second item replaces the second instance**, and so on."

即**按出现顺序替换**，不是按参数下标寻址。参数多于占位符时多余项被忽略；参数不足时文档未规定，本实现保留剩余 `%@` 原样（已在代码注释标注为实现选择）。

## 四、对比：aps 内 vs aps 外

| 放法 | 结果 |
| --- | --- |
| `{"aps":{...}, "gameID":"1"}` | ✅ 生效，出现在 `userInfo` |
| `{"aps":{"gameID":"1"}, ...}` | ❌ **静默丢弃**，无报错 |

`split_payload()` 对 aps 内未知键采取"过滤丢弃"而非抛错 —— 这正是 APNs 的真实行为；`dropped_aps_keys()` 把它显式暴露出来供开发期自检。

## 五、环境与运行

- Python 3.9+（无第三方依赖）：`python payload.py` → `OK: 53 assertions passed`
  （自检本体已拆到 `selfcheck_payload.py`，单独运行亦可）
- Swift 版为语义镜像，本仓库无 Swift 工具链，走人工审查

## 六、关键代码

```python
aps, custom = split_payload({"aps": {"alert": "Hi", "gameID": "1"}, "acme": 42})
# aps    -> {'alert': 'Hi'}          gameID 被丢弃
# custom -> {'acme': 42}

localize("%@ wants to play with %@", ["Shelly", "Rick"])
# -> 'Shelly wants to play with Rick'   （按出现顺序）

validate_payload({"aps": {"badge": 0}})                 # []    角标清除
validate_payload({"aps": {"content-available": 0}})     # 非空  只能为 1
validate_payload({"aps": {"relevance-score": 1.5}})     # 非空  越界
```

## 七、性能边界

- **载荷按字节计**：普通通知 4096 字节、VoIP 5120 字节。中文按 UTF-8 是 3 字节/字，别用字符数估算。
- 载荷**不允许压缩**。
- 自定义数据只适合放"标识符"级别的信息（如 `gameID`），正文应让 App 收到后自行拉取。
- 文档明确要求：**敏感数据不要放进载荷**；必须放时应先加密，再用 Notification Service Extension 在端侧解密。

## 八、注意事项与常见坑

1. **自定义键写进 `aps`** —— 静默失效。CI 里加一条 `dropped_aps_keys(payload) == []` 的断言能挡掉大半此类问题。
2. **`content-available` 写成 `true` 或 `0`** —— 文档要求数值 `1`。
3. **critical alert 用了 `sound: "default"` 字符串** —— critical 必须用 sound 字典。
4. **本地化参数按下标替换** —— 与文档的"按出现顺序"不符，在模板含多个 `%@` 且顺序与参数顺序不一致时才暴露。
5. **用字符数估算 4KB** —— 国际化文案下极易超限，触发 `PayloadTooLarge`（且该 reason **禁止重试**）。
6. **`mutable-content` 忘了配 Notification Service Extension** —— 标志位给了但端侧没扩展，等于没给。
7. **Live Activity 的 `relevance-score` 被普通校验规则拦掉** —— 它不受 0~1 限制，校验函数需要 `is_live_activity` 开关。

## 九、参考资料（本轮实际读过的原文）

- [Generating a remote notification — Apple Developer Documentation](https://developer.apple.com/documentation/usernotifications/generating-a-remote-notification)（Table 1/2/3 全表、Listing 1~3、载荷上限、敏感数据与端侧解密要求）
- [Pushing background updates to your App — Apple Developer Documentation](https://developer.apple.com/documentation/usernotifications/pushing-background-updates-to-your-app)（`content-available` 与 `aps` 内不得含交互键）
- [Sending notification requests to APNs — Apple Developer Documentation](https://developer.apple.com/documentation/usernotifications/sending-notification-requests-to-apns)（4096 / 5120 字节上限、禁止压缩）
