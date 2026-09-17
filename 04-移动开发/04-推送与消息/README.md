# 推送与消息

> 研究移动端「服务端 → 设备」的消息投递:平台推送通道(APNs / FCM / 国内厂商通道)、设备令牌生命周期、离线排队与 TTL、静默推送与后台唤醒。2026-09-17 经自动巡检按类目拓展规则新增(登记 `STATE.md` 索引表第 55 项)。

**为什么单独开一个子类目**:推送是移动端唯一"由操作系统代持长连接"的通道 —— 应用自己不持有 socket,而由系统维护一条共享的长连接,这条设计直接决定了后台存活、省电策略(Doze / iOS 后台限制)与消息可达性的全部边界。它既不是普通网络编程(连接不由应用管理),也不是普通后台任务(唤醒时机不由应用决定),故单列。

## 核心研究主题

- **三层投递模型**:应用服务端 → 平台网关(APNs / FCM)→ 设备上的系统进程 → 目标应用。**应用服务端永远不直连设备**
- **令牌(token)生命周期**:token 按「应用 + 设备 + 环境」分配,**不永久有效** —— 重装、恢复备份、系统升级、安全轮换都可能换 token;服务端必须处理失效反馈并清理,否则会打出"重试风暴"
- **消息类型**:FCM 分 `notification`(系统自动展示)与 `data`(交给应用处理)两类;APNs 用 `aps` 字典 + 自定义键,`content-available: 1` 表示静默唤醒
- **离线排队与 TTL**:这是两大平台差异最大的地方 —— 离线期间各自的排队上限与丢弃策略不同,直接影响消息类应用的完整性语义
- **优先级**:两端都提供"高 / 普通"两档;高优先级用于用户可感知的即时消息,滥用会被限流
- **静默推送**:两端都支持,但 iOS 的限制显著更严(节流、延迟、可能被抑制),不适合承担"必达"语义
- **厂商通道(国内)**:小米 / 华为 / OPPO / vivo 等自建通道 + 激进的后台进程管理,导致 Android 侧实际可达性取决于 OEM,与 FCM 的行为不一致

## 关键事实对照(来源见下,两家官方文档本轮不可达,已标注)

| 维度 | APNs | FCM |
| --- | --- | --- |
| 提供方 / 传输 | Apple / HTTP-2 | Google / HTTP v1(旧 Server Key 2024 起废弃) |
| 令牌失效 | **可随时静默失效**,必须处理反馈 | 无固定有效期,需定期刷新 |
| 离线排队 | 每个应用**只保留最新一条**,旧消息被静默覆盖 | 按设备排队(有上限)+ TTL 窗口 |
| 静默推送 | 严格限制,可能被节流/抑制 | 相对宽松,但受 Doze 与 OEM 限制 |
| 权限 | 始终需要用户授权 | Android 13+ 起也需要显式授权 |

**共同结论:推送是 best-effort 而非事务性投递**。所有实现都必须把推送当作"提示有新数据",真正的数据同步仍要由应用拉取 —— 这一点决定了推送协议设计的正确姿势。

## 待研究

- [ ] APNs 官方文档全文(`developer.apple.com/documentation/usernotifications`)—— 本轮仅取到页面外壳,正文需另行精读
- [ ] FCM 官方消息生命周期与 `concept-options`(`firebase.google.com/docs/cloud-messaging/*`)—— 本轮网络不可达
- [ ] FCM 下行消息的 `data` / `notification` 组合在各应用状态下(前台 / 后台 / 被杀)的分发差异
- [ ] 国内厂商通道接入(小米 / 华为 / OPPO / vivo)与厂商级 token 映射
- [ ] 长连接自建方案与厂商通道的混用策略(自建 TCP + 厂商兜底)
- [ ] Doze / App Standby 对高优先级消息的实际豁免边界
- [ ] Notification Service Extension 与 Content Extension 的富媒体与端侧解密
- [ ] token 失效检测与批量清理(反馈服务 + 错误码分类)
- [ ] 推送链路的可观测性(到达率 / 打开率 / token 有效率)

## 参考资料(本轮实际读过的来源)

- [Push Notification Handling: FCM vs APNs — Push0](https://push0.com/blog/push-notification-handling-firebase-cloud-messaging-vs-apns/) — 架构对照、token 非永久性、静默推送限制、推送为 best-effort
- [APNs vs. FCM – Complete Developer Guide — Push0](https://push0.com/blog/apns-vs-fcm-complete-developer-guide/) — `aps` 字典与 FCM 载荷结构、通知/数据载荷分工、token 失效原因清单
- [What are mobile push notifications? — PubNub](https://www.pubnub.com/blog/mobile-push-notifications/) — 三种载荷形态(`aps` / FCM notification / FCM data)、`content-available` 唤醒语义、优先级与富推送
- [出海推送完全指南:FCM 与 APNs 接入 — Pushwoosh](https://www.pushwoosh.com/blog/fcm-apns-going-global-guide) — 4 KB 载荷上限、Android 13+ 授权变更、FCM HTTP v1 与 Server Key 废弃

> **口径说明**:以上四条均为厂商工程博客(非官方规范),其中 FCM 载荷上限、TTL 具体数值等条目在不同来源间表述不一致;`firebase.google.com` 与 `developer.apple.com` 的官方正文本轮均未能取得(前者 443 连接超时),故本 README **不给出未经验证的数值结论**,相关条目已列入「待研究」。**本目录暂标记为「索引已建、原理待补」**,后续轮次优先补齐官方源。
