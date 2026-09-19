# FCM v1 消息模型与平台覆写

> FCM HTTP v1 的 `Message` 是一个**跨平台的信封**：顶层 `notification` / `data` 是通用语义，`android` / `apns` / `webpush` 是**平台覆写**。理解"谁覆盖谁"以及"哪些字段必须原样、哪些字段被 SDK 变换"，是接 FCM 时最容易出错的地方。

> **来源口径**：本轮 `firebase.google.com` 与 `developers.google.com` **网络不可达**，因此本 demo 以 **Google 官方仓库 `firebase-admin-python` 的源码**（`_messaging_encoder.py`、`messaging.py`）为语义依据，逐条复刻其校验与编码规则。凡官方文档才能给出的运行期数值（各状态下的分发差异等）一律**不写**。

## 一、目标必须恰好一个

官方 SDK 在序列化末尾做的最后一步校验：

```python
target_count = sum(t in result for t in ['fid', 'token', 'topic', 'condition'])
if target_count != 1:
    raise ValueError('Exactly one of fid, token, topic or condition must be specified.')
```

即 **fid / token / topic / condition 四选一，且只能选一个**。给 0 个或 2 个都会在本地就被拒，不会打到服务端。

`topic` 还有一层规范化：先去掉 `/topics/` 前缀，再校验字符集 `^[a-zA-Z0-9-_.~%]+$`。所以 `"/topics/news"` 与 `"news"` 等价；`"news!"` 会直接报 `Malformed topic name.`。

## 二、Android 覆写：两套优先级别搞混

这是本 demo 最想钉住的一点 —— `AndroidConfig` 和 `AndroidNotification` **各有一个 priority**，取值范围完全不同：

| 位置 | 取值 | 编码结果 |
| --- | --- | --- |
| `AndroidConfig.priority`（传输层） | `high` / `normal` | **原样保留小写** |
| `AndroidNotification.priority`（展示层） | `min` / `low` / `default` / `high` / `max` | 转成 `PRIORITY_MIN` … `PRIORITY_MAX` |

把 `max` 写到 `AndroidConfig.priority` 上会被 SDK 直接拒绝（`'AndroidConfig.priority must be "high" or "normal".'`）。

同类的"小写输入、大写输出"还有两个：

- `AndroidNotification.visibility`：`private` / `public` / `secret` → `PRIVATE` / `PUBLIC` / `SECRET`
- `AndroidNotification.proxy`：`allow` / `deny` / `if_priority_lowered` → `ALLOW` / `DENY` / `IF_PRIORITY_LOWERED`

## 三、TTL 的编码

`AndroidConfig.ttl` 接受秒数或 `timedelta`，非负；**负数直接报错**。编码规则是：

```python
seconds = int(math.floor(total_seconds))
nanos   = int((total_seconds - seconds) * 1e9)
return f'{seconds}.{str(nanos).zfill(9)}s' if nanos else f'{seconds}s'
```

所以 `0 → "0s"`、`3 → "3s"`、`3.5 → "3.500000000s"`（纳秒固定 9 位，注意小数会**放大 9 个数量级**，肉眼极易看错）。

## 四、`remove_null_values` 的边界

SDK 在编码每一步都会剔除 `None` / `[]` / `{}`：

```python
{k: v for k, v in dict_value.items() if v not in [None, [], {}]}
```

**关键边界**：`False` 和 `0` **不会被剔除**（它们不等于 `None`/`[]`/`{}`）。所以 `direct_boot_ok=False` 会真的发出去，而不是被当成"未设置"。这一点在组合布尔开关时经常踩。

## 五、APNs 覆写：布尔位写成数值 1

`ApnsConfig.payload.aps` 里，`content_available` 与 `mutable_content` 只有在 **严格等于 `True`** 时才写入，且写成**数值 `1` 而不是布尔 `true`**：

```python
if aps.content_available is True:
    result['content-available'] = 1
```

这与 APNs 官方文档对 `aps` 字典的要求一致（见本目录上级《载荷与aps字典》）。另外 `thread_id` 会映射成带连字符的 `thread-id`；`custom_data` 里的键被摊平为 `aps` 的**同级**（又是"自定义键不能进 aps"这条规则）。

## 六、对比：顶层字段 vs 平台覆写

| 写法 | 作用范围 | 说明 |
| --- | --- | --- |
| 顶层 `notification` | 全平台 | 通用字段：`title` / `body` / `image` |
| 顶层 `data` | 全平台 | **必须是字符串到字符串**的映射 |
| `android.*` | 仅 Android | 优先级、TTL、collapse_key、通知渠道等 |
| `apns.*` | 仅 iOS | 完整 `aps` 字典 + 自定义数据 |
| `webpush.*` | 仅 Web | 含 `fcm_options.link`（必须是 HTTPS） |

`data` 的"值必须是字符串"这条由 `check_string_dict` 强制：**非字符串键或非字符串值都会在本地报错**，而不是静默转成字符串。

## 七、环境与运行

- Python 3.9+（无第三方依赖）：`python fcm_message.py` → `OK: 70 assertions passed`
  （自检本体已拆到 `selfcheck_fcm_message.py`，单独运行亦可）
- Kotlin 版为语义镜像，本仓库无 Kotlin 工具链，走人工审查

## 八、关键代码

```python
encode_message({"topic": "/topics/news"})              # {'topic': 'news'}
encode_message({"token": "t", "topic": "n"})           # 抛错：目标只能一个
encode_ttl(3.5)                                        # '3.500000000s'
encode_android_notification({"priority": "max"})["notification_priority"]
# 'PRIORITY_MAX'
encode_aps({"content_available": True})                # {'content-available': 1}
remove_null_values({"a": None, "d": 0, "e": False})    # {'d': 0, 'e': False}
```

## 九、性能边界

- `analytics_label`：正则 `^[a-zA-Z0-9-_.~%]{1,50}$`，**长度 1~50**，空串与 51 字符都会被拒，空格与 `/` 也不允许
- `AndroidNotification.color`：`#RRGGBB`（6 位）；呼吸灯的 `LightSettings.color` 额外接受 `#RRGGBBAA`（8 位）
- `apns-expiration` / `apns-priority` 等 APNs 头通过 `ApnsConfig.headers` 传入，值同样必须是字符串
- 发送端点：`https://fcm.googleapis.com/v1/projects/{project_id}/messages:send`

## 十、注意事项与常见坑

1. **把 `max` 当传输层优先级** —— 那是展示层的取值，会被本地校验拦下。
2. **TTL 写成 `"3s"` 字符串** —— SDK 只接受数字或 `timedelta`。
3. **TTL 用负数表示"立即"** —— 直接报错；"立即或丢弃"要用 `0`。
4. **`data` 里塞数字或布尔** —— `check_string_dict` 会拒绝，必须先 `str()`。
5. **以为 `False` 会被当未设置剔除** —— 不会，它会真发出去。
6. **`content_available` 写 `1` 或 `"true"`** —— 只有严格 `True` 才会写入 `content-available: 1`。
7. **自定义数据写进 `aps`** —— 必须用 `ApnsConfig.payload.custom_data`，它会被摊平为 `aps` 的同级。
8. **topic 名带 `/topics/` 前缀就以为要另存一份** —— SDK 会剥掉，是同一个 topic。
9. **`UNREGISTERED` 当成配额问题** —— 它是令牌失效，必须清理令牌（见《设备令牌生命周期》）。

## 十一、参考资料（本轮实际读过的原文）

- [firebase-admin-python: `firebase_admin/_messaging_encoder.py`（官方仓库源码）](https://github.com/firebase/firebase-admin-python/blob/master/firebase_admin/_messaging_encoder.py)——`MessageEncoder` / `_Validators`：target 唯一性、topic 规范化、TTL 编码、三套枚举映射、`remove_null_values`、analytics_label 正则、颜色正则
- [firebase-admin-python: `firebase_admin/messaging.py`（官方仓库源码）](https://github.com/firebase/firebase-admin-python/blob/master/firebase_admin/messaging.py)——`FCM_URL` / `FCM_BATCH_URL` / `FCM_ERROR_TYPES`
  > 二者本轮均通过 jsDelivr 镜像（`cdn.jsdelivr.net/gh/firebase/firebase-admin-python@master/...`）取到源文件实读。
