# 静默推送与后台唤醒

> 静默推送（`content-available`）是**唯一能唤醒 App** 的推送形态，代价是：系统把它当作**低优先级**，不保证投递、会被节流、会互相顶掉。**它不能承担"必达"语义**。

来源：Apple 官方《Pushing background updates to your App》《Sending notification requests to APNs》全文实读。

## 一、它是什么

背景通知是一种**不弹 alert、不播声音、不改角标**的远程通知。系统唤醒 App，给一点时间去服务端拉数据。

一个合法的载荷长这样（`aps` 里只有 `content-available`，自定义键在外面）：

```json
{
   "aps" : { "content-available" : 1 },
   "acme1" : "bar",
   "acme2" : 42
}
```

请求头必须配套：`apns-push-type: background` + `apns-priority: 5`。
文档原文：`aps` 字典**不得包含任何会触发用户交互的键**（alert / sound / badge）。

前置条件：Signing & Capability → Background Modes → 勾选 **Remote notification**（watchOS 加在 WatchKit Extension 上）。

## 二、三条"持有 - 延迟"副作用（最容易被忽略）

文档明确列出系统收到背景通知后可能**持有并延迟投递**，产生三个副作用：

| 事件 | 结果 |
| --- | --- |
| 收到**新**的背景通知 | **丢弃旧的那条，只保留最新一条** |
| App 被用户强制退出 / 系统杀掉 | **丢弃持有的通知** |
| 用户主动启动 App | **立即投递持有的通知** |

工程含义：如果你用静默推送做"数据同步触发"，那么**连发 5 条只会有 1 条生效**；而且用户一旦划掉了 App，这次触发就彻底消失。

## 三、节流与预算

- **节流**：系统把背景通知视为低优先级，**不保证投递**；数量过多会被节流。文档给的尺度是 *"don't try to send more than two or three per hour"*。本 demo 取上界 **3 条/小时**，并把窗口实现为滑动窗口（口径选择，README 已标注）。
- **预算**：系统唤醒 App 后有 **30 秒**执行任务并调用 completion handler。iOS 走 `application(_:didReceiveRemoteNotification:fetchCompletionHandler:)`，watchOS 走 extension delegate 的对应方法。

## 四、对比：三种推送形态

| 形态 | 用户可见 | 保证投递 | 典型用途 |
| --- | --- | --- | --- |
| alert 通知 | 是 | best-effort（但会入库重试） | 用户可感知的消息 |
| 背景通知 | 否 | **更弱**，可节流可丢弃 | 内容预取 |
| VoIP / 特殊 push type | 视类型 | 各类型不同 | 来电、Live Activity、FileProvider 等 |

## 五、环境与运行

- Python 3.9+（无第三方依赖）：`python background_delivery.py` → `OK: 37 assertions passed`
- Swift 版为语义镜像，本仓库无 Swift 工具链，走人工审查

## 六、关键代码

```python
d = BackgroundDelivery()
d.send("x", 0); d.send("y", 1)
d.held["apns_id"]        # 'y'   —— 新的顶掉旧的
d.force_quit()           # 持有的通知被丢弃
d.user_launch()          # None  —— 杀掉之后启动，什么都收不到

d.run_task(30.0)         # 'completed'
d.run_task(30.1)         # 'expired'
```

节流是**滑动窗口**而非固定小时桶：t=0/10/20 各发一条后，t=30 的第 4 条被节流；等到 t=3620（三条都已滑出 1 小时窗口）才恢复。

## 七、性能边界

- 频率：**2~3 条/小时**是文档给的量级，不是保证值（实际允许条数"取决于当前状况"）
- 预算：**30 秒**；超时会被系统记录，进而影响后续的唤醒意愿
- 载荷：仍然受 4096 字节上限约束
- 优先级：`apns-priority: 5`；**不要**用 `10`（那是立即投递语义），也不要用 `1`（不唤醒设备）

## 八、注意事项与常见坑

1. **拿静默推送做"必达"** —— 这是设计误用。文档原话就是 *the system doesn't guarantee their delivery*。
2. **连发多条期望都生效** —— 只会保留最新一条。
3. **忘记勾选 Remote notification 后台模式** —— 通知能到系统但不会唤醒 App。
4. **`aps` 里混进 alert/sound/badge** —— 那就不再是背景通知，语义被悄悄改变。
5. **30 秒内没调 completion handler** —— 系统会判定本次后台执行不合格。
6. **用 `apns-priority: 10` 发背景通知** —— 与文档推荐的 5 不符，且失去了"按电量考量"的意图。
7. **依赖静默推送做消息类业务的完整性** —— 应改为"推送提示 + App 自行拉取"的双段式。
8. **App 被强制退出后指望静默推送恢复** —— 文档明确说持有的通知会被丢弃。

## 九、参考资料（本轮实际读过的原文）

- [Pushing background updates to your App — Apple Developer Documentation](https://developer.apple.com/documentation/usernotifications/pushing-background-updates-to-your-app)（背景通知定义、载荷样例、三条副作用、2~3 条/小时、30 秒预算、后台模式开关）
- [Sending notification requests to APNs — Apple Developer Documentation](https://developer.apple.com/documentation/usernotifications/sending-notification-requests-to-apns)（`apns-push-type`、`apns-priority` 10/5/1 语义）
- [Generating a remote notification — Apple Developer Documentation](https://developer.apple.com/documentation/usernotifications/generating-a-remote-notification)（`content-available` 键定义）
