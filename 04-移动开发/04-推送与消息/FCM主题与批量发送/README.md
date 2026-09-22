# FCM 主题订阅与批量发送

> 主题（topic）是 FCM 的**扇出原语**：一次订阅把 N 个设备归到一个名字下，一条消息打到这个名字上。但"名字"本身有一套不显眼的规则——前缀会被悄悄剥掉、字符集窄得超出直觉、目标四选一少一个多一个都报错。
>
> **来源口径**：本轮 `firebase.google.com` 与 `developers.google.com` **网络不可达**（与 2026-09-19 那批 demo 同一情况），因此语义依据是 **Google 官方仓库 [`firebase/firebase-admin-python`](https://github.com/firebase/firebase-admin-python) 的源码**（`messaging.py`、`_messaging_encoder.py`、`_messaging_utils.py`，master 分支实读）。凡官方文档才能给出的运行期数值（例如条件表达式允许的主题数）一律不写。

## 一、topic 名：`/topics/` 前缀会被剥掉，然后过一道窄字符集

官方 `MessageEncoder.sanitize_topic_name` 的全部逻辑：

```python
if not topic:
    return None
prefix = '/topics/'
if topic.startswith(prefix):
    topic = topic[len(prefix):]
if not re.match(r'^[a-zA-Z0-9-_\.~%]+$', topic):
    raise ValueError('Malformed topic name.')
return topic
```

三个后果：

1. **空串 / None 不报错，返回 None**——因为 topic 是可选字段，"没给 topic"和"给了个空 topic"在编码层是同一回事；
2. **前缀只剥一次**，而且是先剥再校验，所以 `/topics//topics/a` 会剩下 `/topics/a`，其中的 `/` 让整条被判非法；
3. 合法字符集是 URI 的 unreserved 集合：`A-Z a-z 0-9 - _ . ~ %`。

### 那个看起来像区间的 `-`

`[a-zA-Z0-9-_\.~%]` 里，`0-9` 之后紧跟 `-_`，很容易被读成"从 `9`（0x39）到 `_`（0x5F）的区间"，那样就会把 `: ; < = > ? @` 全放进来。**实测不是**：Python 把这里的 `-` 当字面量处理，0x3a~0x40 一个都不匹配（`selfcheck` §1 对这 7 个字符逐个断言）。

## 二、主题管理走的是 IID，不是 FCM v1

订阅/退订请求打的是 **`iid.googleapis.com`**，不是 `fcm.googleapis.com/v1`：

```python
IID_URL = 'https://iid.googleapis.com'
IID_HEADERS = {'access_token_auth': 'true'}
data = {'to': '/topics/<name>', 'registration_tokens': [...]}
url = f'{IID_URL}/iid/v1:batchAdd'      # 或 iid/v1:batchRemove
```

两点：

- 请求头带 `access_token_auth: true`，这是 IID 端点特有的；
- **`to` 字段里必须带 `/topics/` 前缀**（与消息编码时"剥掉前缀"正好相反），SDK 在缺前缀时自动补。

响应按 `results` 数组逐条判定，**有 `error` 键就算失败**，下标就是 `ErrorInfo.index`：

```python
for index, result in enumerate(resp['results']):
    if 'error' in result:
        self._failure_count += 1
        self._errors.append(ErrorInfo(index, result['error']))
```

也就是说**订阅是部分成功语义**：100 个 token 里 3 个失败，`success_count=97`，没有任何异常被抛出。

## 三、目标必须恰好一个

`MessageEncoder.default` 在序列化末尾做最后一道校验：

```python
target_count = sum(t in result for t in ['fid', 'token', 'topic', 'condition'])
if target_count != 1:
    raise ValueError('Exactly one of fid, token, topic or condition must be specified.')
```

注意这个校验发生在 `topic` 已经被 `sanitize_topic_name` 处理**之后**——所以 `topic=''` 会先变成 `None`、再被 `remove_null_values` 丢掉，最后表现为"零个目标"而不是"topic 非法"。

`condition` 在 SDK 里只做 `check_string(..., non_empty=True)`——**语法和主题数上限都不由 SDK 校验**。本 demo 因此不给任何条件表达式的数值约束。

## 四、TTL 编码：字符串秒 + 9 位纳秒

```python
seconds = int(math.floor(total_seconds))
nanos = int((total_seconds - seconds) * 1e9)
if nanos:
    return f'{seconds}.{str(nanos).zfill(9)}s'
return f'{seconds}s'
```

| 输入 | 输出 |
| --- | --- |
| `3` | `"3s"` |
| `3.5` | `"3.500000000s"` |
| `timedelta(seconds=120)` | `"120s"` |
| `0` | `"0s"` |
| `-1` | `ValueError: ... must not be negative.` |
| `"3s"` | `ValueError: ... must be a duration ...` |

一个浮点细节：`1.000001` 在这里会得到 `"1.000000999s"` 而不是 `"1.000001000s"`——`(1.000001-1)*1e9` 在二进制浮点下是 `999.999...`，`int()` 直接砍成 999。官方实现就是这么写的，本 demo **原样记录不改正**。

另外 `collapse_key` 的官方 docstring 给了一个数量约束：**"A maximum of 4 different collapse keys may be active at a given time."**

## 五、`send_each` 不是批量 API，是并发扇出

这是最容易误解的一点。官方 `send_each` 的实现：

```python
if len(messages) > 500:
    raise ValueError('messages must not contain more than 500 elements.')
with concurrent.futures.ThreadPoolExecutor(max_workers=len(message_data)) as executor:
    responses = list(executor.map(send_data, message_data))
```

- **上限 500 条**；
- 每条消息**独立一次 POST** 到 `/v1/projects/{id}/messages:send`，不是 multipart 批量；
- `max_workers = len(messages)`——条数即并发度；
- 单条抛异常被捕获成 `SendResponse(exception=...)`，**不影响其它条**。

所以 `FCM_BATCH_URL = 'https://fcm.googleapis.com/batch'` 这个常量虽然还在，但 `send_each` 走的是**并发 + 逐条**，不是 HTTP batch。

还有一个边界：`messages=[]` 时 `max_workers=0`，会被标准库拒绝。

### "成功"的官方定义比直觉严

```python
def success(self):
    return self._message_id is not None and not self._exception
```

**响应体为 None 但也没抛异常 → 失败**。而且响应体里没有 `name` 键会直接抛 `ValueError`，不是静默失败。

## 六、代码结构

| 文件 | 内容 |
| --- | --- |
| `python/fcm_topic.py` | topic 规范化、`_Validators`、IID 请求/响应、`encode_ttl`、错误码表 |
| `python/fcm_batch.py` | 目标四选一、`send_each` 计划与扇出、`SendResponse`/`BatchResponse` |
| `python/main.py` | 七段演示输出 |
| `python/selfcheck_fcmtopic.py` | 98 条断言 |
| `go/fcm_topic.go` | 同一套规则的 Go 版 |

```bash
cd python && python main.py && python selfcheck_fcmtopic.py
```

## 七、三个总结

1. **主题名在"编码消息"和"管理订阅"两个方向上的规则是反的**：发消息时剥 `/topics/`，订阅时补 `/topics/`。
2. **部分成功是常态**。主题订阅和 `send_each` 都不抛聚合异常，必须自己数 `failure_count` / 遍历 `errors`。
3. **`send_each` 的代价是 N 次 HTTP 请求**。500 条消息 = 500 次 POST，不是 1 次。

## 参考资料（实际读过的来源）

- [`firebase/firebase-admin-python` — `firebase_admin/messaging.py`](https://github.com/firebase/firebase-admin-python/blob/master/firebase_admin/messaging.py) — `send_each` 的 500 上限与 ThreadPoolExecutor 扇出、`FCM_BATCH_URL`/`IID_URL`、`FCM_ERROR_TYPES`、`TopicManagementResponse`
- [`firebase/firebase-admin-python` — `firebase_admin/_messaging_encoder.py`](https://github.com/firebase/firebase-admin-python/blob/master/firebase_admin/_messaging_encoder.py) — `sanitize_topic_name`、`check_string`、`encode_ttl`、目标四选一
- [`firebase/firebase-admin-python` — `firebase_admin/_messaging_utils.py`](https://github.com/firebase/firebase-admin-python/blob/master/firebase_admin/_messaging_utils.py) — `AndroidConfig.collapse_key` 的"最多 4 个"docstring、各类 Error 定义
