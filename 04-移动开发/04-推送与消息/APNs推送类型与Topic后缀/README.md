# APNs 推送类型与 Topic 后缀

> `apns-push-type` 不是"随便填个 alert 就行"的装饰性头部——它决定了**topic 长什么样**、**优先级只能是几**、**哪些平台收得到**，甚至**能不能用证书认证**。官方对它的措辞是：不一致时 "APNs may return an error, **delay the delivery** of the notification, or **drop it altogether**"——也就是说填错了**可能没有任何报错**，只是消息不到。
>
> 来源：Apple《Sending notification requests to APNs》的 "Know when to use push types" 定义列表（DocC JSON 原文实读，11 个取值逐条）；liveactivity 的 topic 写法另用《Starting and updating Live Activities with ActivityKit push notifications》交叉核对。

## 一、11 个取值全表

| push type | `apns-topic` | 优先级 | 不可用平台 | 认证 |
| --- | --- | --- | --- | --- |
| `alert` | bundle ID | 10 / 5 | — | 任意 |
| `background` | bundle ID | **只能 5** | — | 任意 |
| `complication` | bundle ID + `.complication` | 未约束 | macOS / tvOS / iPadOS | 任意 |
| `controls` | bundle ID + `.push-type.controls` | 未约束 | 未给 | 任意 |
| `fileprovider` | bundle ID + `.pushkit.fileprovider` | 未约束 | watchOS | 任意 |
| `liveactivity` | bundle ID + `.push-type.liveactivity` | 未约束 | watchOS / macOS / tvOS | 任意 |
| `location` | bundle ID + `.location-query` | 10 / 5 | macOS / tvOS / watchOS | **仅 token** |
| `mdm` | 证书 subject 的 UID | 未约束 | watchOS | 任意 |
| `pushtotalk` | bundle ID + `.voip-ptt` | 未约束 | watchOS / macOS / tvOS | 任意 |
| `voip` | bundle ID + `.voip` | 未约束 | watchOS | 任意 |
| `widgets` | bundle ID + `.push-type.widgets` | 未约束 | 未给 | 任意 |

`apns-priority` 省略时 APNs 按 **10** 处理（官方："If you omit this header, APNs sets the notification priority to 10"）。载荷上限 **4096** 字节，VoIP 是 **5120**。

## 二、三条硬约束

### 1. background 只能是 5，写 10 是错误

官方原文：

> Always use priority `5`. Using priority `10` is an error.

这是唯一一个明文禁止某优先级的 type。直觉上"背景刷新要快"很常见，但这条是被文档钉死的。

### 2. location 只支持 token 认证

> The `location` push type supports only token-based authentication.

用证书连 APNs 的服务端推 location 会失败。另外它也是少数给了优先级建议的：需要 Location Push Service Extension 立即响应就用 10，否则 5。

### 3. mdm 的 topic 不来自 bundle ID

> you must use the topic from the **UID attribute in the subject of your MDM push certificate**

所以 mdm 是唯一一个 `apns-topic` 与 bundle ID 完全无关的取值。

## 三、官方文档里的两处 suffix 不一致

这一节是本 demo 最值得记的部分。

在《Sending notification requests to APNs》的 DocC JSON 里，两个后缀的**字面量**是这样的：

| push type | 该页 JSON 字面量 | 更权威写法 | 出处 |
| --- | --- | --- | --- |
| `complication` | `h.complication` | `.complication` | 强推断（其余后缀均以 `.` 开头，`h` 无法解释） |
| `liveactivity` | `push-type.liveactivity` | `.push-type.liveactivity` | ActivityKit 页明文写 `<your bundleID>.push-type.liveactivity` |

`liveactivity` 这条是**可证**的：ActivityKit 那篇里同时出现了

```
apns-topic: com.example.apple-samplecode.Emoji-Rangers.push-type.liveactivity
```

也就是说按 `apns-push-type` 那张表的字面量拼，会得到 `com.example.MyApppush-type.liveactivity`——**少一个点**，而且不会有任何报错，只是 Live Activity 永远收不到更新。

`complication` 的 `h.complication` 没找到第二份官方来源能直接印证，所以本 demo 把它登记为 `canonical='.complication'` 并在 README 里标注为**推断**，不冒充官方结论。

> 代码里 `topic_for(name, bundle)` 默认取 `canonical`；`topic_for(name, bundle, canonical=False)` 会返回那张表的字面量，用于对照。

## 四、证书 vs token 的能力差

官方一句话概括：

> **Certificate-based connection supports only a subset of push types and token-based connection supports all push-types.**

具体差在哪，要看证书里的两个扩展：

- `1.2.840.113635.100.6.3.6` —— WatchKit services（`complication` 用）
- `1.2.840.113635.100.6.3.4` —— VoIP services（`voip` 用）

> These extensions list all the push topics allowed for your certificate. **If a push topic for a specific push type isn't listed, you can't use the certificate to send a notification of that push type.**

所以"我用证书一直好好的，换成 liveactivity 就不行"不是 bug，是证书里没这个 topic。

## 五、代码结构

| 文件 | 内容 |
| --- | --- |
| `python/push_type.py` | 11 个取值的登记表 + `topic_for` / `payload_limit` / `validate` |
| `python/main.py` | 五段演示输出 |
| `python/selfcheck_pushtype.py` | 71 条断言 |
| `go/push_type.go` | 同一张表的 Go 版 |

```bash
cd python && python main.py && python selfcheck_pushtype.py
```

`validate()` 返回**问题列表**而不是布尔值——因为一个请求可以同时犯好几个错（topic 不对 + 平台不对 + 优先级不对），只回一个 bool 会让人一次只能发现一个。

## 六、两个容易想当然的地方

1. **"不可用"不等于"会报错"**。文档只说 "isn't available on"，demo 里据此判定为问题；但真实 APNs 的行为没有官方承诺，可能静默丢弃。所以 `validate()` 是**服务端的自检工具**，不是 APNs 的行为仿真。
2. **`alert` 与 `background` 的 topic 相同**，只有 push type 不同。这意味着单看 `apns-topic` 无法区分一条推送是不是静默推送——必须同时看 `apns-push-type` 和载荷里有没有 `content-available`。

## 参考资料（实际读过的来源）

- [Sending notification requests to APNs](https://developer.apple.com/documentation/usernotifications/sending-notification-requests-to-apns) — 请求头表、`apns-push-type` 的 11 个取值定义列表、证书扩展 OID、4096/5120 载荷上限、默认优先级
- [Starting and updating Live Activities with ActivityKit push notifications](https://developer.apple.com/documentation/ActivityKit/starting-and-updating-live-activities-with-activitykit-push-notifications) — `apns-topic` 写作 `<bundleID>.push-type.liveactivity`（用于交叉核对）
- [Keeping your complications up to date](https://developer.apple.com/documentation/ClockKit/keeping-your-complications-up-to-date) — complication 相关（未找到 topic 后缀的第二处明文，故不据此改正）
