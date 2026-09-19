# 设备令牌生命周期

> device token 是「设备 + 应用」的地址，会**静默失效**。服务端若不清理失效令牌，就会对一串死地址反复重试 —— 这是推送系统里最典型的**重试风暴**来源。

来源：Apple 官方《Registering your app with APNs》《Handling notification responses from APNs》全文实读；FCM 侧错误码取自官方 Admin SDK 源码（`firebase.google.com` 本轮不可达，已在下面标注口径）。

## 一、令牌是什么

APNs 在能发通知之前必须知道设备地址，这个地址就是 **device token**：

- 对「**设备 + 应用**」唯一；
- **同一台设备上的两个 App 不能复用同一个 token**，即使来自同一台设备；
- App 每次启动都要调用注册接口拿 token，再上报给 provider；
- token 由系统下发，**长度不要做任何假设**（文档明说 *Don't make assumptions about device token size*）。

## 二、什么时候会换 token

文档列出的三种情况：**用户从备份恢复设备**、**在新设备上安装你的 App**、**重装操作系统**。

因此文档给了一条很硬的规矩：

> **Never cache device tokens in local storage.**

正确姿势是**每次启动都问系统要**，拿到就上报。服务端侧则要做好"同一个 (设备, 应用) 换了 token"的轮换处理 —— 旧 token 应当立即作废。

## 三、注册会失败，且失败很常见

文档列出的失败原因：**设备没连网**、**APNs 服务器不可达**、**缺少正确的 code-signing entitlement**。

处理方式：**置一个标志位，稍后再试** —— 不要弹错、不要阻塞启动、更不要把失败当成"用户拒绝"。

## 四、一个用户对应多个 token

因为一个用户可能有多台设备，服务端必须把 token 与**用户账户**关联存储，并按账户取出**一组** token 来发。这也是为什么"按 token 建主键"比"按用户建主键"更合理。

## 五、失效清理（重试风暴的防线）

APNs 侧：文档明列**不可重试**的 reason 里，有四个是令牌级失效 ——
`BadDeviceToken` / `DeviceTokenNotForTopic` / `ExpiredToken` / `Unregistered`。
其中 `Unregistered` 的响应体还会带上 `timestamp`（毫秒 epoch，APNs 确认该 token 失效的时刻）。

**注意区分**：`PayloadTooLarge`、`Forbidden` 也不可重试，但它们**不是**令牌失效，清理令牌是错的。

FCM 侧：官方 Admin SDK 把错误码映射成异常类型，其中只有 `UNREGISTERED` → `UnregisteredError` 是令牌级：

| 错误码 | 异常类型 |
| --- | --- |
| `UNREGISTERED` | `UnregisteredError` |
| `SENDER_ID_MISMATCH` | `SenderIdMismatchError` |
| `QUOTA_EXCEEDED` | `QuotaExceededError` |
| `THIRD_PARTY_AUTH_ERROR` / `APNS_AUTH_ERROR` | `ThirdPartyAuthError` |

> **口径说明**：上表来自官方 `firebase-admin-python` 仓库的 `_MessagingService.FCM_ERROR_TYPES`（`firebase.google.com` 本轮网络不可达，无法与官方 REST 文档交叉核对），仅作 SDK 层语义的依据。

## 六、环境与运行

- Python 3.9+（无第三方依赖）：`python token_registry.py` → `OK: 35 assertions passed`
- Kotlin 版为语义镜像，本仓库无 Kotlin 工具链，走人工审查

## 七、关键代码

```python
r.register(user_id="u1", device_id="d1", app_id="com.a", token="t1", now=0)   # 'new'
r.register(user_id="u1", device_id="d1", app_id="com.a", token="t1", now=60)  # 'unchanged' 幂等
r.register(user_id="u1", device_id="d1", app_id="com.a", token="t2", now=120) # 'rotated'  旧 t1 作废

r.prune([("x1", "Unregistered"), ("x2", "TooManyRequests"),
         ("x3", "PayloadTooLarge")])                                          # 只删掉 1 个
```

跨 App 复用会被显式拒绝：把 `com.a` 的 token 报给 `com.b` 会抛 `TokenRegistryError`，且不影响 `com.b` 已有的令牌。

## 八、性能边界

- 注册频率：**每次 App 启动一次**；上报是幂等的，重复上报不产生新记录
- 失效清理：应当**随响应实时做**，而不是攒着批量做 —— 攒批期间你仍在往死地址发
- 批量发送：一次 fan-out 到某用户的全部设备 token，因此**失效比例**直接放大成你的无效 QPS

## 九、注意事项与常见坑

1. **把 token 缓存到 UserDefaults / SharedPreferences** —— 文档明令禁止。
2. **假设 token 长度固定** —— 文档明说不要假设，按 `Data` 的字节原样透传与存储。
3. **只在首次安装时上报 token** —— 备份恢复 / 换设备 / 重装系统后你就失联了。
4. **把 `PayloadTooLarge` 也当成令牌失效清理** —— 是载荷问题，清令牌会误伤活用户。
5. **`TooManyRequests` 就删令牌** —— 恰恰相反，它是"延迟后重试"。
6. **注册失败就弹错误给用户** —— 文档要求的是置标志位后重试。
7. **一个用户只存一个 token** —— 多设备用户会随机丢设备。
8. **清理后没做可观测** —— 需要能看到失效速率；失效速率突增通常意味着 App 侧上报逻辑坏了。

## 十、参考资料（本轮实际读过的原文）

- [Registering your app with APNs — Apple Developer Documentation](https://developer.apple.com/documentation/usernotifications/registering-your-app-with-apns)（token 唯一性、禁止本地缓存、换 token 的三种情形、注册失败处理、多设备）
- [Handling notification responses from APNs — Apple Developer Documentation](https://developer.apple.com/documentation/usernotifications/handling-notification-responses-from-apns)（不可重试 reason 集合、`Unregistered` 的 `timestamp`）
- [firebase-admin-python: firebase_admin/messaging.py（官方仓库源码）](https://github.com/firebase/firebase-admin-python/blob/master/firebase_admin/messaging.py)（`FCM_ERROR_TYPES` 错误码到异常类型的映射；本轮通过 jsDelivr 镜像取到源文件实读）
