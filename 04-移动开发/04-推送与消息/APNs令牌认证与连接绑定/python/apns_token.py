"""APNs 令牌认证的服务端侧判定：密钥作用域配额 + 刷新窗口 + 连接绑定。

规则全部出自 Apple 官方《Establishing a token-based connection to APNs》
与《Handling notification responses from APNs》（DocC 原文实读）：

1. 刷新窗口 —— "Refresh your token no more than once every 20 minutes and
   no less than once every 60 minutes"；"APNs rejects any request whose token
   contains a timestamp that's more than one hour old"；"APNs report an error
   if you use a new token more than once every 20 minutes on the same
   connection"。
2. 连接绑定 —— 首次推送时 APNs 把 "team ID + 关联的 bundle ID" 绑到这条连接上；
   之后换团队、推新加的 bundle ID 都报错。一条连接只能服务一个开发者账号。
3. 作用域 —— team-scoped 钥匙每环境最多 2 把；topic-specific 钥匙沙盒/生产各
   最多 200 把、每把最多 400 个 topic，且在同一环境里最多有 1 把 "related key"。
"""
TEAM_KEYS_PER_ENV = 2
TOPIC_KEYS_PER_ENV = 200
TOPICS_PER_TOPIC_KEY = 400
MAX_TOKEN_AGE = 3600          # iat 超过 1 小时 -> 403 ExpiredProviderToken
MIN_REFRESH_GAP = 1200        # 同一连接上换钥匙快于 20 分钟 -> 429

ENVS = ('sandbox', 'production')


class KeyScopeError(ValueError):
    """密钥本身不合规（创建阶段就应被拒绝）。"""


class SigningKey:
    """APNs 认证令牌签名密钥（.p8）的服务端侧登记项。

    kind='team'  -> team-scoped：可用于本团队任意 topic，但限定单一环境
    kind='topic' -> topic-specific：只能用于 topics 里的 topic
    """

    def __init__(self, kid, kind, env, team, topics=None, related=None):
        if kind not in ('team', 'topic'):
            raise KeyScopeError('kind must be team or topic')
        if env not in ENVS:
            raise KeyScopeError('env must be sandbox or production')
        if kind == 'team' and topics:
            raise KeyScopeError('team-scoped key has no topic list')
        if kind == 'topic':
            if not topics:
                raise KeyScopeError('topic-specific key needs at least one topic')
            if len(topics) > TOPICS_PER_TOPIC_KEY:
                raise KeyScopeError('at most %d topics per topic-specific key' % TOPICS_PER_TOPIC_KEY)
        self.kid = kid
        self.kind = kind
        self.env = env
        self.team = team
        self.topics = frozenset(topics) if topics else frozenset()
        self.related = related

    def __repr__(self):
        return '<SigningKey %s %s/%s team=%s>' % (self.kid, self.kind, self.env, self.team)


class KeyRegistry:
    """按团队 + 环境记账，复刻官方的钥匙数量上限。"""

    def __init__(self):
        self.keys = {}

    def add(self, key):
        if key.kid in self.keys:
            raise KeyScopeError('duplicate key id')
        cnt = self._count(key.team, key.kind, key.env)
        limit = TEAM_KEYS_PER_ENV if key.kind == 'team' else TOPIC_KEYS_PER_ENV
        if cnt >= limit:
            raise KeyScopeError('too many %s keys in %s (%d)' % (key.kind, key.env, limit))
        if key.related is not None:
            if key.kind != 'topic':
                raise KeyScopeError('only topic-specific keys may have a related key')
            rel = self.keys.get(key.related)
            if rel is None or rel.env != key.env or rel.team != key.team:
                raise KeyScopeError('related key must exist in the same environment')
            if rel.related not in (None, key.kid):
                raise KeyScopeError('a topic-specific key has at most one related key')
            rel.related = key.kid
        self.keys[key.kid] = key
        return key

    def _count(self, team, kind, env):
        return sum(1 for k in self.keys.values()
                   if k.team == team and k.kind == kind and k.env == env)

    def get(self, kid):
        return self.keys.get(kid)


def allowed_topics(key, registry):
    """topic-specific 钥匙可用的 topic 集合。

    官方只说 "you can use authentication tokens from both the topic-based key
    and its related key"，没有明说主题集合是否取并集；本实现取并集，
    并在 README 里标注这一口径。
    """
    if key.kind == 'team':
        return None                      # None 表示"本团队任意 topic"
    topics = set(key.topics)
    if key.related:
        rel = registry.get(key.related)
        if rel is not None:
            topics |= set(rel.topics)
    return frozenset(topics)


def token_age_verdict(now, iat):
    """只判令牌新鲜度。返回 (http, reason) 或 None。"""
    if iat is None:
        return (403, 'MissingProviderToken')
    if now - iat > MAX_TOKEN_AGE:
        return (403, 'ExpiredProviderToken')
    return None


class Connection:
    """一条 HTTP/2 连接的绑定状态。首推之后团队、钥匙、环境都固定下来。"""

    def __init__(self, conn_id):
        self.conn_id = conn_id
        self.bound = False
        self.team = None
        self.first_kid = None
        self.first_kind = None
        self.env = None
        self.topics = None                # None = 团队内任意
        self.last_iat = None
        self.accepted = 0

    def _bind(self, key, registry, iat):
        self.bound = True
        self.team = key.team
        self.first_kid = key.kid
        self.first_kind = key.kind
        self.env = key.env
        self.topics = allowed_topics(key, registry)
        self.last_iat = iat
        self.accepted += 1
        return {'status': 'accepted', 'http': 200, 'reason': None, 'bound': True}

    def _reject(self, http, reason):
        return {'status': 'rejected', 'http': http, 'reason': reason, 'bound': self.bound}

    def push(self, key, topic, now, iat, registry):
        """判定一次推送能否走这条连接。返回 (http, reason) 形态的 dict。"""
        verdict = token_age_verdict(now, iat)
        if verdict is not None:
            return self._reject(*verdict)

        if not self.bound:
            return self._bind(key, registry, iat)

        if iat != self.last_iat and 0 <= iat - self.last_iat < MIN_REFRESH_GAP:
            return self._reject(429, 'TooManyProviderTokenUpdates')
        if key.team != self.team:
            return self._reject(403, 'Forbidden')
        if key.env != self.env:
            return self._reject(403, 'BadEnvironmentKeyIdInToken')
        related_ok = (self.first_kind == 'topic' and key.kind == 'topic'
                      and key.kid != self.first_kid
                      and self._is_related(key.kid, registry))
        if key.kid != self.first_kid and not related_ok:
            return self._reject(403, 'UnrelatedKeyIdInToken')
        topics = self.topics if key.kid == self.first_kid else allowed_topics(key, registry)
        if topics is None:
            topics = self.topics
        if topics is not None and topic not in topics:
            return self._reject(403, 'TopicDisallowed')
        self.last_iat = iat
        self.accepted += 1
        return {'status': 'accepted', 'http': 200, 'reason': None, 'bound': True}

    def _is_related(self, kid, registry):
        first = registry.get(self.first_kid)
        return first is not None and first.related == kid
