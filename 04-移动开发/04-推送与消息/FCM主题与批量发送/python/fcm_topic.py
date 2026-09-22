"""FCM 主题管理与消息时限：逐行移植 `firebase-admin-python` 的实现。

**口径声明**：本轮 `firebase.google.com` 与 `developers.google.com` 的网络请求全部
失败（与 2026-09-19 那批 demo 同一情况），因此语义依据是 **Google 官方仓库
`firebase/firebase-admin-python` 的源码**：`firebase_admin/messaging.py`、
`_messaging_encoder.py`、`_messaging_utils.py`。凡官方文档才能给出的运行期数值
（如条件表达式的主题数上限）一律不写。
"""
import datetime
import math
import numbers
import re

IID_URL = 'https://iid.googleapis.com'
IID_HEADERS = {'access_token_auth': 'true'}
TOPIC_PREFIX = '/topics/'
# 官方 SDK 原样的字符集。注意 `9-_` 里的 `-` 是**字面量**而不是区间起点，
# 实测 0x3a~0x40（: ; < = > ? @）都不匹配。
TOPIC_NAME_RE = re.compile(r'^[a-zA-Z0-9-_\.~%]+$')

# 官方 SDK 的 _MessagingService.FCM_ERROR_TYPES
FCM_ERROR_TYPES = {
    'APNS_AUTH_ERROR': 'ThirdPartyAuthError',
    'QUOTA_EXCEEDED': 'QuotaExceededError',
    'SENDER_ID_MISMATCH': 'SenderIdMismatchError',
    'THIRD_PARTY_AUTH_ERROR': 'ThirdPartyAuthError',
    'UNREGISTERED': 'UnregisteredError',
}

# 官方 docstring：AndroidConfig.collapse_key
MAX_ACTIVE_COLLAPSE_KEYS = 4


class ErrorInfo:
    """主题管理响应里的一条失败项（官方 `messaging.ErrorInfo`）。"""

    def __init__(self, index, reason):
        self.index = index
        self.reason = reason

    def __repr__(self):
        return '<ErrorInfo index=%d reason=%r>' % (self.index, self.reason)


def sanitize_topic_name(topic):
    """官方 `MessageEncoder.sanitize_topic_name`：剥前缀 + 校验字符集。

    空值/空串返回 None（**不报错**），因为 topic 是可选字段。
    """
    if not topic:
        return None
    prefix = TOPIC_PREFIX
    if topic.startswith(prefix):
        topic = topic[len(prefix):]
    if not TOPIC_NAME_RE.match(topic):
        raise ValueError('Malformed topic name.')
    return topic


def check_string(label, value, non_empty=False):
    """官方 `_Validators.check_string`。"""
    if value is None:
        return None
    if not isinstance(value, str):
        if non_empty:
            raise ValueError('%s must be a non-empty string.' % label)
        raise ValueError('%s must be a string.' % label)
    if non_empty and not value:
        raise ValueError('%s must be a non-empty string.' % label)
    return value


def check_string_list(label, value):
    """官方 `_Validators.check_string_list`：None 与空表都保留原样。"""
    if value is None or value == []:
        return None
    if not isinstance(value, list):
        raise ValueError('%s must be a list of strings.' % label)
    non_str = [t for t in value if not isinstance(t, str) or not t]
    if non_str:
        raise ValueError('%s must not contain non-string values.' % label)
    return value


def encode_ttl(ttl):
    """官方 `MessageEncoder.encode_ttl`：`"3s"` / `"3.500000000s"`。"""
    if ttl is None:
        return None
    if isinstance(ttl, numbers.Number):
        ttl = datetime.timedelta(seconds=ttl)
    if not isinstance(ttl, datetime.timedelta):
        raise ValueError('AndroidConfig.ttl must be a duration in seconds or an '
                         'instance of datetime.timedelta.')
    total_seconds = ttl.total_seconds()
    if total_seconds < 0:
        raise ValueError('AndroidConfig.ttl must not be negative.')
    seconds = int(math.floor(total_seconds))
    nanos = int((total_seconds - seconds) * 1e9)
    if nanos:
        return '%d.%ss' % (seconds, str(nanos).zfill(9))
    return '%ds' % seconds


def build_topic_request(tokens, topic, operation):
    """官方 `make_topic_management_request` 的请求构造部分。

    tokens 支持单个字符串或列表；topic 缺 `/topics/` 前缀时自动补上。
    """
    if isinstance(tokens, str):
        tokens = [tokens]
    if not isinstance(tokens, list) or not tokens:
        raise ValueError('Tokens must be a string or a non-empty list of strings.')
    invalid = [t for t in tokens if not isinstance(t, str) or not t]
    if invalid:
        raise ValueError('Tokens must be non-empty strings.')
    if not isinstance(topic, str) or not topic:
        raise ValueError('Topic must be a non-empty string.')
    if not topic.startswith(TOPIC_PREFIX):
        topic = TOPIC_PREFIX + topic
    if operation not in ('iid/v1:batchAdd', 'iid/v1:batchRemove'):
        raise ValueError('unknown topic management operation')
    return {
        'url': '%s/%s' % (IID_URL, operation),
        'headers': dict(IID_HEADERS),
        'json': {'to': topic, 'registration_tokens': tokens},
    }


class TopicManagementResponse:
    """官方 `messaging.TopicManagementResponse`：按 results 里有没有 error 计数。"""

    def __init__(self, resp):
        if not isinstance(resp, dict) or 'results' not in resp:
            raise ValueError('Unexpected topic management response: %s.' % (resp,))
        self._success_count = 0
        self._failure_count = 0
        self._errors = []
        for index, result in enumerate(resp['results']):
            if 'error' in result:
                self._failure_count += 1
                self._errors.append(ErrorInfo(index, result['error']))
            else:
                self._success_count += 1

    @property
    def success_count(self):
        return self._success_count

    @property
    def failure_count(self):
        return self._failure_count

    @property
    def errors(self):
        return self._errors


def classify_fcm_error(code):
    """按官方 FCM_ERROR_TYPES 把错误码映射成异常类型名；未登记的返回 None。"""
    return FCM_ERROR_TYPES.get(code)
