# APNs 请求与响应

> 复刻 APNs 的 HTTP/2 请求语义、离线存储策略与响应分派规则。核心结论：**APNs 是 best-effort 服务** —— 它每个 bundle ID 只替你保留一条通知，会重排你的消息，也会静默丢弃。

来源：Apple 官方《Sending notification requests to APNs》《Handling notification responses from APNs》两篇全文实读（链接见文末）。

## 一、为什么不是"发个 POST 就完事"

推送链路是**三方**的：你的 provider → APNs 网关 → 设备上的系统进程 → App。应用服务端**永远不直连设备**，连接由系统代持。这带来三个直接后果：

1. 你不能假设消息按顺序到达 —— 文档明说 **APNs may reorder notifications**；
2. 你不能假设消息一定到达 —— 存储、节流、丢弃都在文档许可范围内；
3. 反馈回路只有一条 —— 每个 POST 的响应，所以**必须逐条处理响应**，否则失效令牌会积累成重试风暴。

## 二、请求头语义（逐条来自文档）

| 头 | 必填性 | 语义与坑 |
| --- | --- | --- |
| `:method` | 必需 | 只能是 `POST`，否则 405 |
| `:path` | 必需 | `/3/device/<device_token>`，token 是十六进制字节；**不要假设 token 长度固定** |
| `authorization` | token 认证时必需 | `bearer <provider_token>`；证书认证时该头被忽略 |
| `apns-push-type` | watchOS 6+ 必需，其余推荐 | 必须如实反映载荷；**mismatch 时 APNs 可能报错、延迟投递或直接丢弃** |
| `apns-id` | 可选 | 规范 UUID（32 个小写十六进制，8-4-4-4-12）。省略则 APNs 代生成并从响应返回 |
| `apns-expiration` | 可选 | UNIX epoch 秒。`0` = 只投递一次**不存储**；非 0 = 存储并重试到该时刻（**不保证**，可能超期送达） |
| `apns-priority` | 可选，默认 `10` | `10` 立即；`5` 按设备电量考量；`1` 电量优先于一切且**不唤醒设备** |
| `apns-topic` | 通常必需 | 一般是 bundle ID，可带 push type 后缀 |
| `apns-collapse-id` | 可选 | 用于合并多条通知，**不得超过 64 字节**（按字节不是按字符） |

**载荷上限**：普通通知 4 KB（4096 字节），VoIP 5 KB（5120 字节）；**不允许压缩过的 JSON**。

### HPACK 编码建议（文档原文，易被忽略）

APNs 要求 HPACK 且**动态表很小**。多条 stream 并发时应：`apns-id` / `apns-expiration` / `apns-collapse-id` 编成 *literal without indexing*；`apns-topic` / `apns-push-type` / `apns-priority` 首次 *incremental indexing*、后续 *literal without indexing*；其余字段一律 *literal without indexing*。实际收益是**不撑爆动态表**，从而避免后续请求被迫增大。

## 三、离线存储：一条而非一队

文档原文最关键的一句：**APNs stores only one notification per bundle ID**。

- 存储时长：**30 天或更短**，取决于 `apns-expiration`；
- 同一 (token, bundle ID) 存多条时，**通常保留最新一条**，但文档明确写了 *this behavior isn't always guaranteed when multiple notifications are stored in a short duration*；
- `apns-priority 5` 和 `1` 的消息**可能被分组、突发投递**，也可能被节流、入库、乃至不投递；
- 单次"投递尝试"内部可能跨多个网络接口重试，因此**非 0 的 expiration 只是"尽力"**。

工程含义：**推送只能当"有新数据了"的提示**，真正的数据一致性必须由 App 自己拉取来保证。

## 四、响应分派

状态码表（文档列了 10 个）：`200 成功 / 400 坏请求 / 403 证书或 token 错误 / 404 :path 非法 / 405 :method 非法 / 410 token 不再活跃 / 413 载荷过大 / 429 同一 token 请求过多 / 500 内部错误 / 503 服务关闭`。

错误体是 JSON 字典，含 `reason`（32 个取值之一）；`timestamp` 键**只在 `reason` 为 `Unregistered` 时出现**，单位是毫秒 epoch。同一份 JSON 也可能出现在连接终止的 `GOAWAY` 帧里。

文档给出的重试规则（原文复述）：

- 5XX 的载荷**在 15 分钟之后**可以重试，重试时可以用退避；
- 大多数 4XX 在**修好 `reason` 指明的问题后**可以重试；
- **不要重试** `BadDeviceToken`、`DeviceTokenNotForTopic`、`Forbidden`、`ExpiredToken`、`Unregistered`、`PayloadTooLarge`；
- 只有 `TooManyRequests` 是"延迟后重试"；
- 4XX 会**降低你后续的发送能力**；APNs 会因错误过多断开连接，其中 `BadDeviceToken` 断开得更快；
- **`410` 不被视为 error condition**（它是令牌生命周期的正常终点）。

> **口径提示**：文档"After 15 minutes, you can retry JSON payloads that receive response status codes that begin with 5XX"这句话的断句存在歧义（是"15 分钟后才能重试"还是"可在 15 分钟窗口内重试"），本 demo 按字面实现为 `retryAfterBackoff`，**不给出更强的数值承诺**。

## 五、对比：APNs 与常见"消息队列"心智模型

| 维度 | 消息队列 | APNs |
| --- | --- | --- |
| 顺序 | FIFO 保证 | 可能重排 |
| 积压 | 队列长度可见 | 每 bundle ID **只留 1 条** |
| 失败 | 明确 ack/nack | reason 分四类动作，部分**禁止重试** |
| 过期 | TTL 到点丢弃 | `apns-expiration` 只是上限，**可能超期送达** |

## 六、环境与运行

- Python 3.9+（无第三方依赖）：`python apns_request.py` → `OK: 73 assertions passed`
  （自检本体已拆到 `selfcheck_apns_request.py`，单独运行亦可；拆分仅为满足单文件 ≤300 行）
- Swift 版为语义镜像，需 Xcode / swiftc 才能编译；本仓库环境无 Swift 工具链，走人工审查

## 七、关键代码

```python
h = build_headers(token, topic="com.example.MyApp", push_type="alert")
# {'apns-priority': '10', 'apns-expiration': '0', ':path': '/3/device/<token>', ...}

classify_response(410, "Unregistered")   # -> 'drop_token'
classify_response(429, "TooManyRequests")# -> 'retry_later'
classify_response(400, "PayloadTooLarge")# -> 'never_retry'
classify_response(500)                   # -> 'retry_5xx'
```

`ApnsStore` 复刻存储语义：`expiration=0` → `not_stored`（只投递一次）；非 0 → 入存储，同 key 后来者覆盖；`keep_latest=False` 模式下先到者胜，用来复现文档"不保证保留最新"的那句。

## 八、性能边界

- 载荷：4096 字节普通 / 5120 字节 VoIP；`apns-collapse-id` 64 字节
- 单连接并发 stream 数**不保证**：文档明说别假设具体数量；用 token 认证时，在发出第一个带有效 token 的请求之前**只允许 1 条 stream**
- 连接可复用"数小时到数天"；空闲 1 小时后建议发 HTTP/2 PING
- 每次建连前应做**不带缓存的 DNS 查询**，把流量摊到所有 APNs 服务器

## 九、注意事项与常见坑

1. **别把 `apns-expiration=0` 当成"立即送达"** —— 它的准确含义是"只尝试一次、不入库"，设备离线即丢。
2. **`apns-priority=1` 不唤醒设备**，拿它发即时消息会静默失败。
3. **自定义键必须放在 `aps` 之外**（见同目录《载荷与aps字典》）；放在 `aps` 内会被忽略，且不会报错。
4. **`Unregistered` 一定要清理令牌**；文档说除非 App 再次上报同一个 token，否则不要再发。
5. **不要缓存设备 token 到本地**（见同目录《设备令牌生命周期》）。
6. **`GOAWAY` 帧里也可能带 `reason`**，只解析 HTTP 响应的实现会漏掉连接级错误。
7. **每次 POST 都要看 status**，别做 fire-and-forget：4XX 会拖慢你的发送能力。
8. 文档建议的 provider token 更新频率：**不超过每 20 分钟一次**（`TooManyProviderTokenUpdates`）。

## 十、参考资料（本轮实际读过的原文）

- [Sending notification requests to APNs — Apple Developer Documentation](https://developer.apple.com/documentation/usernotifications/sending-notification-requests-to-apns)（本轮通过 DocC JSON 接口取全文实读：请求头表、push type、HPACK 建议、载荷上限、最佳实践）
- [Handling notification responses from APNs — Apple Developer Documentation](https://developer.apple.com/documentation/usernotifications/handling-notification-responses-from-apns)（状态码表、`reason` 32 个取值、重试规则、GOAWAY）
- [Registering your app with APNs — Apple Developer Documentation](https://developer.apple.com/documentation/usernotifications/registering-your-app-with-apns)
- [Pushing background updates to your App — Apple Developer Documentation](https://developer.apple.com/documentation/usernotifications/pushing-background-updates-to-your-app)
