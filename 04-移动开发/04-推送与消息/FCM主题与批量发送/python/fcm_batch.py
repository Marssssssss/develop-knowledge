"""FCM v1 的批量发送：`send_each` 的扇出模型与逐条结果。

移植自 `firebase/firebase-admin-python` 的 `messaging.py`：
`_MessagingService.send_each` 与 `SendResponse` / `BatchResponse`。
"""
import fcm_topic as topic

FCM_URL = 'https://fcm.googleapis.com/v1/projects/{0}/messages:send'
FCM_BATCH_URL = 'https://fcm.googleapis.com/batch'
SEND_EACH_LIMIT = 500
FCM_HEADERS = {
    'X-GOOG-API-FORMAT-VERSION': '2',
}
TARGET_KEYS = ('fid', 'token', 'topic', 'condition')


class SendResponse:
    """单条消息的发送结果。

    官方定义：success = `message_id is not None and not exception`。注意
    **响应体为 None 但也没抛异常时算失败**，所以"没报错"不等于"成功"。
    """

    def __init__(self, resp=None, exception=None):
        self.message_id = None
        self.exception = exception
        if resp is not None:
            if not isinstance(resp, dict) or 'name' not in resp:
                raise ValueError('Unexpected batch response: %s.' % (resp,))
            self.message_id = resp['name']

    @property
    def success(self):
        return self.message_id is not None and not self.exception


class BatchResponse:
    """一批消息的聚合结果。"""

    def __init__(self, responses):
        self.responses = list(responses)

    @property
    def success_count(self):
        return sum(1 for r in self.responses if r.success)

    @property
    def failure_count(self):
        return len(self.responses) - self.success_count


def encode_message(**fields):
    """官方 `MessageEncoder.default` 的目标校验：四选一，且**恰好一个**。

    topic 会先经 sanitize_topic_name 剥前缀，所以空 topic 会变成 None 而不计入。
    """
    result = {}
    for key in TARGET_KEYS:
        value = fields.get(key)
        if key == 'topic':
            value = topic.sanitize_topic_name(value)
        result[key] = value
    result = {k: v for k, v in result.items() if v is not None}
    target_count = sum(1 for t in TARGET_KEYS if t in result)
    if target_count != 1:
        raise ValueError('Exactly one of fid, token, topic or condition must be specified.')
    return result


def message_data(message, dry_run=False):
    """官方 `_message_data`：dry_run 走 `validate_only`。"""
    data = {'message': message}
    if dry_run:
        data['validate_only'] = True
    return data


def plan_send_each(messages):
    """官方 `send_each` 的**前置校验与执行计划**（不真正发请求）。

    返回计划 dict；不合规直接抛 ValueError。
    """
    if not isinstance(messages, list):
        raise ValueError('messages must be a list of messaging.Message instances.')
    if len(messages) > SEND_EACH_LIMIT:
        raise ValueError('messages must not contain more than 500 elements.')
    if not messages:
        # 官方实现会把 max_workers=len(message_data) 传给 ThreadPoolExecutor，
        # 0 会被标准库拒绝。
        raise ValueError('max_workers must be greater than 0')
    return {
        'url': FCM_URL,
        'requests': [message_data(m) for m in messages],
        'max_workers': len(messages),
        'concurrent': True,
    }


def send_each(messages, transport):
    """按官方扇出模型执行：每条消息独立一次 POST，逐条收集结果。

    transport 是 `fn(index) -> resp_dict`，抛出的异常被捕获成 SendResponse 的
    exception —— 与官方 `send_data` 的行为一致：**单条失败不影响其它条**。
    """
    plan = plan_send_each(messages)
    responses = []
    for index in range(len(messages)):
        try:
            resp = transport(index)
        except Exception as exc:                    # noqa: BLE001 - 与官方一致
            responses.append(SendResponse(resp=None, exception=exc))
        else:
            responses.append(SendResponse(resp, exception=None))
    return BatchResponse(responses), plan
