// Package apnstoken 复刻 APNs 令牌认证的服务端侧判定：密钥作用域配额、
// 令牌刷新窗口与连接绑定状态机。
//
// 规则来源：Apple《Establishing a token-based connection to APNs》与
//《Handling notification responses from APNs》。加密部分（ES256 签名）不含在内，
// 生产实现应使用 crypto/ecdsa + crypto/elliptic 的 P-256。
package apnstoken

const (
	// TeamKeysPerEnv 是 team-scoped 钥匙每环境上限。
	TeamKeysPerEnv = 2
	// TopicKeysPerEnv 是 topic-specific 钥匙每环境上限。
	TopicKeysPerEnv = 200
	// TopicsPerTopicKey 是单把 topic-specific 钥匙能关联的 topic 上限。
	TopicsPerTopicKey = 400
	// MaxTokenAge 是 iat 允许的最大年龄（秒）。
	MaxTokenAge = 3600
	// MinRefreshGap 是同一连接上更换令牌的最小间隔（秒）。
	MinRefreshGap = 1200
)

// Env 取值。
const (
	EnvSandbox    = "sandbox"
	EnvProduction = "production"
)

// Kind 是钥匙作用域类型。
const (
	KindTeam  = "team"
	KindTopic = "topic"
)

// KeyScopeError 表示钥匙本身不合规。
type KeyScopeError string

func (e KeyScopeError) Error() string { return string(e) }

// SigningKey 是一把 APNs 认证令牌签名密钥的登记项。
type SigningKey struct {
	KID     string
	Kind    string
	Env     string
	Team    string
	Topics  map[string]bool
	Related string
}

// NewSigningKey 构造并校验一把钥匙。
func NewSigningKey(kid, kind, env, team string, topics []string, related string) (*SigningKey, error) {
	if kind != KindTeam && kind != KindTopic {
		return nil, KeyScopeError("kind must be team or topic")
	}
	if env != EnvSandbox && env != EnvProduction {
		return nil, KeyScopeError("env must be sandbox or production")
	}
	set := map[string]bool{}
	for _, t := range topics {
		set[t] = true
	}
	if kind == KindTeam {
		if len(set) != 0 {
			return nil, KeyScopeError("team-scoped key has no topic list")
		}
		return &SigningKey{KID: kid, Kind: kind, Env: env, Team: team}, nil
	}
	if len(set) == 0 {
		return nil, KeyScopeError("topic-specific key needs at least one topic")
	}
	if len(set) > TopicsPerTopicKey {
		return nil, KeyScopeError("too many topics for one topic-specific key")
	}
	return &SigningKey{KID: kid, Kind: kind, Env: env, Team: team, Topics: set, Related: related}, nil
}

// Registry 按团队与环境记账。
type Registry struct {
	keys map[string]*SigningKey
}

// NewRegistry 建立空登记表。
func NewRegistry() *Registry {
	return &Registry{keys: map[string]*SigningKey{}}
}

// Add 登记一把钥匙，超配额返回错误。
func (r *Registry) Add(k *SigningKey) error {
	if _, ok := r.keys[k.KID]; ok {
		return KeyScopeError("duplicate key id")
	}
	limit := TeamKeysPerEnv
	if k.Kind == KindTopic {
		limit = TopicKeysPerEnv
	}
	if r.count(k.Team, k.Kind, k.Env) >= limit {
		return KeyScopeError("too many " + k.Kind + " keys in " + k.Env)
	}
	if k.Related != "" {
		if k.Kind != KindTopic {
			return KeyScopeError("only topic-specific keys may have a related key")
		}
		rel, ok := r.keys[k.Related]
		if !ok || rel.Env != k.Env || rel.Team != k.Team {
			return KeyScopeError("related key must exist in the same environment")
		}
		if rel.Related != "" && rel.Related != k.KID {
			return KeyScopeError("a topic-specific key has at most one related key")
		}
		rel.Related = k.KID
	}
	r.keys[k.KID] = k
	return nil
}

func (r *Registry) count(team, kind, env string) int {
	n := 0
	for _, k := range r.keys {
		if k.Team == team && k.Kind == kind && k.Env == env {
			n++
		}
	}
	return n
}

// Get 按 key ID 取钥匙。
func (r *Registry) Get(kid string) *SigningKey { return r.keys[kid] }

// AllowedTopics 返回 topic-specific 钥匙可用的 topic 集合；team-scoped 返回 nil
// 表示"本团队任意 topic"。related key 的 topic 取并集（口径见 README）。
func (r *Registry) AllowedTopics(k *SigningKey) map[string]bool {
	if k.Kind == KindTeam {
		return nil
	}
	out := map[string]bool{}
	for t := range k.Topics {
		out[t] = true
	}
	if k.Related != "" {
		if rel, ok := r.keys[k.Related]; ok {
			for t := range rel.Topics {
				out[t] = true
			}
		}
	}
	return out
}

// Result 是一次推送判定结果。
type Result struct {
	Accepted bool
	HTTP     int
	Reason   string
}

// Connection 是一条 HTTP/2 连接的绑定状态。
type Connection struct {
	ID        string
	Bound     bool
	Team      string
	FirstKID  string
	FirstKind string
	Env       string
	Topics    map[string]bool
	LastIAT   int64
	Accepted  int
}

// NewConnection 建立一条未绑定的连接。
func NewConnection(id string) *Connection {
	return &Connection{ID: id, LastIAT: -1}
}

func rej(http int, reason string, c *Connection) Result {
	return Result{Accepted: false, HTTP: http, Reason: reason}
}

func (c *Connection) bind(k *SigningKey, iat int64, r *Registry) Result {
	c.Bound = true
	c.Team = k.Team
	c.FirstKID = k.KID
	c.FirstKind = k.Kind
	c.Env = k.Env
	c.Topics = r.AllowedTopics(k)
	c.LastIAT = iat
	c.Accepted++
	return Result{Accepted: true, HTTP: 200}
}

// Push 判定一次推送能否走这条连接。
func (c *Connection) Push(k *SigningKey, topic string, now, iat int64, r *Registry) Result {
	if now-iat > MaxTokenAge {
		return rej(403, "ExpiredProviderToken", c)
	}
	if !c.Bound {
		return c.bind(k, iat, r)
	}
	if iat != c.LastIAT && iat-c.LastIAT >= 0 && iat-c.LastIAT < MinRefreshGap {
		return rej(429, "TooManyProviderTokenUpdates", c)
	}
	if k.Team != c.Team {
		return rej(403, "Forbidden", c)
	}
	if k.Env != c.Env {
		return rej(403, "BadEnvironmentKeyIdInToken", c)
	}
	relatedOK := c.FirstKind == KindTopic && k.Kind == KindTopic &&
		k.KID != c.FirstKID && c.isRelated(k.KID, r)
	if k.KID != c.FirstKID && !relatedOK {
		return rej(403, "UnrelatedKeyIdInToken", c)
	}
	topics := c.Topics
	if k.KID != c.FirstKID {
		topics = r.AllowedTopics(k)
	}
	if topics != nil && !topics[topic] {
		return rej(403, "TopicDisallowed", c)
	}
	c.LastIAT = iat
	c.Accepted++
	return Result{Accepted: true, HTTP: 200}
}

func (c *Connection) isRelated(kid string, r *Registry) bool {
	first := r.Get(c.FirstKID)
	return first != nil && first.Related == kid
}
