# 推送与消息

> 研究移动端「服务端 → 设备」的消息投递:平台推送通道(APNs / FCM / 国内厂商通道)、设备令牌生命周期、离线排队与 TTL、静默推送与后台唤醒。2026-09-17 经自动巡检按类目拓展规则新增(登记 `STATE.md` 索引表第 55 项);**2026-09-19 首批 5 个 demo 落地**,官方源已补齐(Apple 官方文档 5 篇全文实读 + FCM 官方 Admin SDK 源码)。

**为什么单独开一个子类目**:推送是移动端唯一"由操作系统代持长连接"的通道 —— 应用自己不持有 socket,而由系统维护一条共享的长连接,这条设计直接决定了后台存活、省电策略(Doze / iOS 后台限制)与消息可达性的全部边界。它既不是普通网络编程(连接不由应用管理),也不是普通后台任务(唤醒时机不由应用决定),故单列。

## 核心研究主题

- **三层投递模型**:应用服务端 → 平台网关(APNs / FCM)→ 设备上的系统进程 → 目标应用。**应用服务端永远不直连设备**
- **token 生命周期**:token 按「应用 + 设备 + 环境」分配,**不永久有效** —— 重装、恢复备份、系统升级、安全轮换都可能换 token;服务端必须处理失效反馈并清理,否则会打出"重试风暴"
- **消息类型**:FCM 分 `notification`(系统自动展示)与 `data`(交给应用处理)两类;APNs 用 `aps` 字典 + 自定义键,`content-available: 1` 表示静默唤醒
- **离线排队与 TTL**:APNs **每个 bundle ID 只保留最新一条**,旧消息被静默覆盖;FCM 侧用 `collapse_key` + TTL 组合
- **优先级**:APNs 三档(10 立即 / 5 按电量 / 1 电量优先且不唤醒);FCM 的 `AndroidConfig.priority` 只有 high / normal 两档
- **静默推送**:APNs 明确"不保证投递"、会被节流(2~3 条/小时)、新的顶掉旧的、App 被杀则丢弃
- **厂商通道(国内)**:小米 / 华为 / OPPO / vivo 等自建通道 + 激进的后台进程管理,导致 Android 侧实际可达性取决于 OEM

## 关键事实对照(APNs 部分已由官方文档逐条核实)

| 维度 | APNs | FCM |
| --- | --- | --- |
| 提供方 / 传输 | Apple / HTTP-2 + TLS 1.2+ | Google / HTTP v1(`/v1/projects/{id}/messages:send`) |
| 载荷上限 | 4 KB(4096),VoIP 5 KB(5120) | 官方源不可达,本目录不写数值 |
| 离线存储 | **每个 bundle ID 只存 1 条**,30 天或更短 | `collapse_key` + `ttl` 组合 |
| 优先级 | 10 / 5 / 1(默认 10) | `high` / `normal` |
| 静默推送 | `content-available=1`,限制严格 | data-only 消息,受 Doze 与 OEM 限制 |
| 令牌失效 | `Unregistered` 等 4 个 reason | `UNREGISTERED` → `UnregisteredError` |

**共同结论:推送是 best-effort 而非事务性投递**。所有实现都必须把推送当作"提示有新数据",真正的数据同步仍要由应用拉取 —— 这一点决定了推送协议设计的正确姿势。

## 已完成 demo

| ID | 路径 | 知识点 | 语言 |
| --- | --- | --- | --- |
| 397 | `APNs请求与响应/` | HTTP/2 请求头语义(apns-id 规范 UUID / expiration 0 与非 0 / priority 10·5·1 / collapse-id ≤64B)、4096·5120 载荷上限、**每 bundle ID 只存 1 条**的存储语义、10 个状态码、32 个 reason、四类重试动作(不可重试 6 个 / 延迟重试仅 TooManyRequests / 5XX / 修后重试)、410 不算 error condition | Python(268+183 行 73 断言实跑) / Swift(166 行) |
| 398 | `载荷与aps字典/` | `aps` 内自定义键被静默忽略、自定义值必须 primitive、alert 字符串↔字典、`badge 0` 清除、critical alert 的 sound 字典(volume 0~1)、interruption-level 四档、relevance-score 0~1(Live Activity 例外)、本地化 `*-loc-args` **按出现顺序**替换 `%@`、4096 按 UTF-8 字节计 | Python(234+127 行 53 断言实跑) / Swift(161 行) |
| 399 | `静默推送与后台唤醒/` | `content-available=1` 且 `aps` 内不得含 alert/sound/badge、push-type=background + priority=5、三条"持有-延迟"副作用(新的顶掉旧的 / 被杀则丢弃 / 用户启动即投递)、2~3 条每小时节流(滑动窗口)、30 秒后台预算 | Python(231 行 37 断言实跑) / Swift(120 行) |
| 400 | `设备令牌生命周期/` | token 对「设备+应用」唯一且**不可跨 App 复用**、每次启动都注册、一用户多设备多 token、APNs 4 个令牌级 reason 与 FCM `UNREGISTERED` 的失效清理、`PayloadTooLarge`/`TooManyRequests` **不是**令牌失效 | Python(231 行 35 断言实跑) / Kotlin(150 行) |
| 401 | `FCMv1消息模型/` | 目标 fid/token/topic/condition **恰好一个**、`/topics/` 前缀剥离与字符集、**两套 priority**(AndroidConfig high·normal 原样 vs AndroidNotification min…max → `PRIORITY_*`)、visibility/proxy 小写转大写、TTL 编码 `"3s"`/`"3.500000000s"`、`remove_null_values` 保留 `False` 与 `0`、`content_available` 只认严格 `True` 且写成数值 1、analytics_label 1~50 字符 | Python(293+134 行 70 断言实跑) / Kotlin(170 行) |

## 待研究

- [x] APNs 官方请求/响应文档全文 —— **2026-09-19 已补齐**(DocC JSON 接口实读)
- [ ] FCM 官方消息生命周期与 `concept-options`(`firebase.google.com` 本轮仍不可达,已用官方 SDK 源码替代)
- [ ] FCM 下行消息 `data` / `notification` 组合在各应用状态下(前台 / 后台 / 被杀)的分发差异(缺官方源,未落 demo)
- [ ] 国内厂商通道接入(小米 / 华为 / OPPO / vivo)与厂商级 token 映射
- [ ] 长连接自建方案与厂商通道的混用策略(自建 TCP + 厂商兜底)
- [ ] Doze / App Standby 对高优先级消息的实际豁免边界
- [ ] Notification Service Extension 与 Content Extension 的富媒体与端侧解密
- [ ] 推送链路的可观测性(到达率 / 打开率 / token 有效率)
- [ ] Live Activity / PushToTalk / VoIP 等特殊 push type 的完整语义

## 参考资料(实际读过的来源)

**Apple 官方文档**(2026-09-19 通过 `developer.apple.com/tutorials/data/documentation/<path>.json` 的 DocC 接口取全文实读):

- [Sending notification requests to APNs](https://developer.apple.com/documentation/usernotifications/sending-notification-requests-to-apns) — 请求头表、push type、HPACK 建议、载荷上限、最佳实践
- [Handling notification responses from APNs](https://developer.apple.com/documentation/usernotifications/handling-notification-responses-from-apns) — 状态码表、`reason` 32 个取值、重试规则、GOAWAY
- [Generating a remote notification](https://developer.apple.com/documentation/usernotifications/generating-a-remote-notification) — Table 1/2/3 全表、本地化规则、敏感数据处理
- [Pushing background updates to your App](https://developer.apple.com/documentation/usernotifications/pushing-background-updates-to-your-app) — 静默推送语义与节流
- [Registering your app with APNs](https://developer.apple.com/documentation/usernotifications/registering-your-app-with-apns) — token 生命周期

**FCM 官方 SDK 源码**(`firebase.google.com` 不可达,改以官方仓库为语义依据;经 jsDelivr 镜像取源文件实读):

- [firebase-admin-python: `_messaging_encoder.py`](https://github.com/firebase/firebase-admin-python/blob/master/firebase_admin/_messaging_encoder.py)
- [firebase-admin-python: `messaging.py`](https://github.com/firebase/firebase-admin-python/blob/master/firebase_admin/messaging.py)

**厂商工程博客**(2026-09-17 建立索引时读过,仅用于架构对照,**不用于数值结论**):

- [Push Notification Handling: FCM vs APNs — Push0](https://push0.com/blog/push-notification-handling-firebase-cloud-messaging-vs-apns/)
- [APNs vs. FCM – Complete Developer Guide — Push0](https://push0.com/blog/apns-vs-fcm-complete-developer-guide/)
- [What are mobile push notifications? — PubNub](https://www.pubnub.com/blog/mobile-push-notifications/)
- [出海推送完全指南:FCM 与 APNs 接入 — Pushwoosh](https://www.pushwoosh.com/blog/fcm-apns-going-global-guide)

> **口径说明**:厂商博客中关于 FCM 载荷上限、TTL 具体数值等条目在不同来源间表述不一致;`firebase.google.com` 与 `developers.google.com` 本轮均不可达,故本目录 **不给出未经验证的 FCM 数值结论**,FCM 侧结论一律以官方 SDK 源码的校验与编码规则为限。
