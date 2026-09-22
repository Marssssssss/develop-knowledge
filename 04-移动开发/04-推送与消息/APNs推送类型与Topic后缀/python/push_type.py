"""APNs 的 `apns-push-type` 与 `apns-topic` 配套规则。

数据全部来自 Apple《Sending notification requests to APNs》的 "Know when to
use push types" 定义列表（DocC JSON 原文实读，共 11 个取值）。每个取值记录了：
topic 后缀、优先级约束、平台可用性、认证方式约束、证书扩展 OID。
"""
PLATFORMS = ('iOS', 'iPadOS', 'macOS', 'tvOS', 'watchOS')
ALL_PLATFORMS = frozenset(PLATFORMS)

VOIP_PAYLOAD_LIMIT = 5120
DEFAULT_PAYLOAD_LIMIT = 4096
DEFAULT_PRIORITY = 10

# 证书里列出"允许哪些 push topic"的两个扩展（官方文档原文给出的 OID）
CERT_EXT_WATCHKIT = '1.2.840.113635.100.6.3.6'
CERT_EXT_VOIP = '1.2.840.113635.100.6.3.4'


class PushType:
    """一个 `apns-push-type` 取值。

    suffix        —— 追加到 bundle ID 末尾形成 topic；None 表示直接用 bundle ID
    suffix_doc    —— 官方文档 JSON 里的**字面量**（用于对照，可能与 suffix 不同）
    priorities    —— 文档明说的优先级取值；None 表示文档未约束
    auth          —— 文档明说的认证方式；None 表示两种都行
    recommended   —— 官方写 "recommended on" 的平台
    unavailable   —— 官方写 "isn't available on" 的平台
    required_on   —— 官方写 "required on" 的平台
    cert_ext      —— 证书扩展 OID（只有 certificate-based 才需要关心）
    """

    def __init__(self, name, suffix=None, suffix_doc=None, priorities=None,
                 auth=None, recommended=(), unavailable=(), required_on=(),
                 cert_ext=(), cert_topic_from=None, canonical=None):
        self.name = name
        self.suffix = suffix
        self.suffix_doc = suffix_doc if suffix_doc is not None else suffix
        self.canonical = canonical
        self.priorities = priorities
        self.auth = auth
        self.recommended = frozenset(recommended)
        self.unavailable = frozenset(unavailable)
        self.required_on = frozenset(required_on)
        self.cert_ext = tuple(cert_ext)
        self.cert_topic_from = cert_topic_from


PUSH_TYPES = {
    'alert': PushType(
        'alert',
        suffix=None,
        priorities=(10, 5),
        recommended=('macOS', 'iOS', 'tvOS', 'iPadOS'),
        required_on=('watchOS',),
    ),
    'background': PushType(
        'background',
        priorities=(5,),
        recommended=('macOS', 'iOS', 'tvOS', 'iPadOS'),
        required_on=('watchOS',),
    ),
    'complication': PushType(
        'complication',
        suffix='.complication',
        suffix_doc='h.complication',       # 官方 JSON 字面量，疑为笔误，见 README
        canonical='.complication',
        recommended=('watchOS', 'iOS'),
        unavailable=('macOS', 'tvOS', 'iPadOS'),
        cert_ext=(CERT_EXT_WATCHKIT,),
    ),
    'controls': PushType(
        'controls',
        suffix='.push-type.controls',
    ),
    'fileprovider': PushType(
        'fileprovider',
        suffix='.pushkit.fileprovider',
        recommended=('macOS', 'iOS', 'tvOS', 'iPadOS'),
        unavailable=('watchOS',),
    ),
    'liveactivity': PushType(
        'liveactivity',
        suffix='push-type.liveactivity',   # 该页给的字面量**不带**前导点
        canonical='.push-type.liveactivity',  # ActivityKit 页写作 <bundleID>.push-type.liveactivity
        recommended=('iOS', 'iPadOS'),
        unavailable=('watchOS', 'macOS', 'tvOS'),
    ),
    'location': PushType(
        'location',
        suffix='.location-query',
        priorities=(10, 5),
        auth=('token',),                   # "supports only token-based authentication"
        recommended=('iOS', 'iPadOS'),
        unavailable=('macOS', 'tvOS', 'watchOS'),
    ),
    'mdm': PushType(
        'mdm',
        recommended=('macOS', 'iOS', 'tvOS', 'iPadOS'),
        unavailable=('watchOS',),
        cert_topic_from='UID attribute of the MDM push certificate subject',
    ),
    'pushtotalk': PushType(
        'pushtotalk',
        suffix='.voip-ptt',
        recommended=('iOS', 'iPadOS'),
        unavailable=('watchOS', 'macOS', 'tvOS'),
    ),
    'voip': PushType(
        'voip',
        suffix='.voip',
        recommended=('macOS', 'iOS', 'tvOS', 'iPadOS'),
        unavailable=('watchOS',),
        cert_ext=(CERT_EXT_VOIP, CERT_EXT_WATCHKIT),
    ),
    'widgets': PushType(
        'widgets',
        suffix='.push-type.widgets',
    ),
}


def is_known(name):
    return name in PUSH_TYPES


def topic_for(name, bundle_id, mdm_topic=None, canonical=True):
    """算出该 push type 应该用的 `apns-topic`。

    canonical=True 时，若该 type 在别处有更权威的写法（见 README §三）就用那个；
    canonical=False 则返回《Sending notification requests to APNs》那张表的字面量。
    """
    spec = PUSH_TYPES[name]
    if spec.cert_topic_from is not None:
        if mdm_topic is None:
            raise ValueError('mdm push type needs the topic from the certificate')
        return mdm_topic
    suffix = spec.suffix
    if canonical and spec.canonical is not None:
        suffix = spec.canonical
    if suffix is None:
        return bundle_id
    return bundle_id + suffix


def has_doc_discrepancy(name):
    """官方文档的字面量与更权威写法不一致的 type。"""
    spec = PUSH_TYPES.get(name)
    return spec is not None and spec.canonical is not None \
        and spec.canonical != spec.suffix_doc


def payload_limit(name):
    """VoIP 是 5 KB，其余 4 KB（官方：4KB(4096) / VoIP 5KB(5120)）。"""
    return VOIP_PAYLOAD_LIMIT if name == 'voip' else DEFAULT_PAYLOAD_LIMIT


def validate(name, topic, priority=None, platform=None, auth='token',
             bundle_id=None):
    """返回问题列表；空列表表示这一组头部组合在文档语义下没问题。"""
    problems = []
    if not is_known(name):
        return ['unknown apns-push-type: %s' % name]
    spec = PUSH_TYPES[name]
    if bundle_id is not None and spec.cert_topic_from is None:
        expected = topic_for(name, bundle_id)
        if topic != expected:
            problems.append('topic mismatch: expected %s' % expected)
    if platform is not None:
        if platform in spec.unavailable:
            problems.append('%s is not available on %s' % (name, platform))
    if priority is not None and spec.priorities is not None:
        if priority not in spec.priorities:
            problems.append('priority %d not allowed for %s' % (priority, name))
    if spec.auth is not None and auth not in spec.auth:
        problems.append('%s supports only %s authentication' % (name, '/'.join(spec.auth)))
    return problems
