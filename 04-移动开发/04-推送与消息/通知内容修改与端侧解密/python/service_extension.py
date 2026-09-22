"""Notification Service App Extension 的处理流水线与超时降级。

规则全部出自 Apple《Modifying content in newly delivered notifications》
（DocC 原文实读）：

- 只有"配置成会显示 alert 的远程通知"才会走扩展；
  应用关了 alert、或载荷里只有 sound / badge，扩展**根本不会被调用**；
- 载荷必须含 `mutable-content: 1`，且 `aps` 里要有 title / subtitle / body；
- `didReceive(_:withContentHandler:)` 只有**大约 30 秒**，超时系统调用
  `serviceExtensionTimeWillExpire()`，此时必须立刻交回内容；
- **两个方法里都没调 completion handler，系统就展示原始内容**——
  也就是把密文原标题原样弹给用户。
"""
MUTABLE_CONTENT_KEY = 'mutable-content'
ENCRYPTED_DATA_KEY = 'ENCRYPTED_DATA'
PLACEHOLDER = '(Encrypted)'

# 官方原文："Your ... method has only about 30 seconds"
DEFAULT_BUDGET = 30.0

# 官方原文：aps 里至少要有这三个之一，扩展才会被启用
ALERT_KEYS = ('title', 'subtitle', 'body')

# 会被系统自动展示、从而"不需要扩展"的字段
NON_ALERT_KEYS = ('sound', 'badge')


class Request:
    """一次远程通知的投递请求。"""

    def __init__(self, aps, custom=None, alerts_enabled=True):
        self.aps = dict(aps)
        self.custom = dict(custom or {})
        self.alerts_enabled = alerts_enabled

    def alert(self):
        a = self.aps.get('alert')
        if isinstance(a, dict):
            return dict(a)
        if isinstance(a, str):
            return {'body': a}
        return {}

    def mutable(self):
        return self.aps.get(MUTABLE_CONTENT_KEY)

    @classmethod
    def secret(cls, ciphertext, title='Secret Message!', category='SECRET',
               body=PLACEHOLDER, alerts_enabled=True):
        """复刻官方 Listing 2 的加密通知载荷。"""
        aps = {'category': category, MUTABLE_CONTENT_KEY: 1,
               'alert': {'title': title, 'body': body}}
        return cls(aps, {ENCRYPTED_DATA_KEY: ciphertext}, alerts_enabled)


def extension_eligible(request):
    """扩展会不会被系统启用。返回 (是否启用, 原因)。"""
    if not request.alerts_enabled:
        return False, 'alerts are disabled for your app'
    # bool 是 int 的子类：裸写 `mutable-content != 1` 会让 JSON 的 true 蒙混过关
    mutable = request.mutable()
    if isinstance(mutable, bool) or mutable != 1:
        return False, 'mutable-content is not 1'
    alert = request.alert()
    if not any(k in alert for k in ALERT_KEYS):
        return False, 'aps.alert has no title/subtitle/body'
    if not alert and any(k in request.aps for k in NON_ALERT_KEYS):
        return False, 'payload specifies only a sound or a badge'
    return True, None


class ServiceExtension:
    """一条通知在扩展里的生命周期。

    clock 是一个可注入的时钟（返回单调递增的秒），用来把 30 秒预算做成
    确定性行为——随机/真实时钟下"通过"只是运气。
    """

    def __init__(self, budget=DEFAULT_BUDGET, clock=None, decrypt=None):
        self.budget = budget
        self.clock = clock
        self.decrypt = decrypt
        self.started_at = None
        self.handler_called = False
        self.from_time_will_expire = False
        self.deadline_notified = False
        self.delivered = None

    def complete(self, content):
        """completion handler —— 交回内容，可以调用**一次**。"""
        if self.handler_called:
            raise RuntimeError('completion handler must be called exactly once')
        self.handler_called = True
        self.delivered = content
        return content

    def time_will_expire(self):
        """系统调用 `serviceExtensionTimeWillExpire()`。

        官方要求：此时必须立刻交回能交出的内容（示例里把 body 清空、
        subtitle 标成 "(Encrypted)"），否则就退回原始内容。
        """
        if self.handler_called:
            return self.delivered
        self.deadline_notified = True
        self.from_time_will_expire = True
        return None

    def did_receive(self, request, elapsed=0.0, decrypt_ok=True):
        """模拟 `didReceive(_:withContentHandler:)` 的主体。"""
        eligible, reason = extension_eligible(request)
        if not eligible:
            return {'employed': False, 'reason': reason, 'delivered': 'original'}
        self.started_at = self.clock() if self.clock else 0.0
        content = dict(request.alert())
        data = request.custom.get(ENCRYPTED_DATA_KEY)
        if data is None:
            self.complete(content)
            return {'employed': True, 'delivered': 'modified', 'decrypted': False,
                    'from_time_will_expire': False}

        if elapsed >= self.budget:
            # 还没来得及调 handler 就被掐掉
            self.time_will_expire()
            expired_content = dict(content)
            expired_content['subtitle'] = PLACEHOLDER
            expired_content['body'] = ''
            self.complete(expired_content)
            return {'employed': True, 'delivered': 'modified', 'decrypted': False,
                    'timed_out': True, 'from_time_will_expire': True}

        if decrypt_ok:
            plaintext = self.decrypt(data) if self.decrypt else data
            content['body'] = plaintext
            self.complete(content)
            return {'employed': True, 'delivered': 'modified', 'decrypted': True,
                    'from_time_will_expire': False}

        content['body'] = PLACEHOLDER
        self.complete(content)
        return {'employed': True, 'delivered': 'modified', 'decrypted': False,
                'from_time_will_expire': False}

    def outcome(self, request):
        """预算耗尽后的最终处置。

        官方：**两个方法里都没调 handler，系统展示原始内容**。
        """
        if self.handler_called:
            return {'delivered': 'modified', 'content': self.delivered}
        original = request.alert()
        return {'delivered': 'original', 'content': original}
