// Idempotency-Key 的强制语义 —— Go 版对照实现（仅标准库）。
//
// 口径来源（实读 www.ietf.org/archive/id/draft-ietf-httpapi-idempotency-key-header-07.txt）：
//   - §2.1  "Idempotency-Key is an Item Structured Header [RFC8941]. Its value MUST be a String"
//     → 线上形态必须带引号：Idempotency-Key: "8e03978e-40d5-43e8-bc93-6894a57f9324"。
//   - §2.2  键 MUST 唯一且 MUST NOT 与不同 payload 一起复用；RECOMMENDED 用 UUID。
//   - §2.3  资源 MAY 要求基于时间的键以便过期清理，SHOULD 公布该策略。
//   - §2.4  fingerprint 可用整个/部分 payload 的校验和、逐字段比对或请求签名生成。
//   - §2.6  三种情形：首次正常处理；重试返回"先前已完成操作的结果（成功或错误）"；
//           并发重试回资源冲突错误。
//   - §2.7  缺头 → 400；同键不同 payload → 422；并发未完成 → 409。
//           客户端 MUST 修正后重试，**409 例外无需修正**。
//   - §5    低熵键会让攻击者猜到别人的键并读到别人缓存的条目 → 应实现
//           "a unique composite key"（客户端键 + 只有资源知道的客户端属性）。
//   - §1    按 RFC 9110，POST 与 PATCH 非幂等，PUT/DELETE 与本 draft 无关。
//
// 运行：go run idempotency.go（本机无 Go 工具链，代码经人工审查 + 结构校验）
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"regexp"
	"strings"
)

// DocLink 是 §2.7 里 Link 头的形态（也可用 RFC 9457 problem+json 表达）。
const DocLink = `<https://developer.example.com/idempotency>; rel="describedby"; type="text/html"`

var sfString = regexp.MustCompile(`^"([ -!#-\[\]-~]*)"$`)
var uuidRe = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)
var randRe = regexp.MustCompile(`^[a-z0-9]{32}$`)

// ParseStructuredString 解析 RFC 8941 的 String（必须带引号）。
func ParseStructuredString(raw string) (string, bool) {
	m := sfString.FindStringSubmatch(strings.TrimSpace(raw))
	if m == nil {
		return "", false
	}
	return m[1], true
}

// IsValidKey 复刻 §5：处理前必须按键的公布格式校验。
func IsValidKey(k string) bool {
	return uuidRe.MatchString(k) || randRe.MatchString(k)
}

// Fingerprint 是 §2.4 的"整个 payload 的校验和"。
func Fingerprint(payload map[string]interface{}) string {
	b, _ := json.Marshal(payload) // Go 的 json 对 map 按 key 排序，天然规范化
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}

// Record 是幂等存储里的一条。
type Record struct {
	Fingerprint string
	State       string // in_progress | done
	Status      int
	Body        interface{}
	ExpiresAt   float64
}

// Response 携带状态码、body 与提示。
type Response struct {
	Status  int
	Body    interface{}
	Headers map[string]string
	Note    string
}

// Middleware 把幂等语义包在业务 handler 外面，按 (clientID, key) 复合键索引。
type Middleware struct {
	TTL        float64
	RequireKey bool
	Clock      float64
	Store      map[string]*Record
}

// NewMiddleware 创建一个中间件。
func NewMiddleware(ttl float64, require bool) *Middleware {
	return &Middleware{TTL: ttl, RequireKey: require, Store: map[string]*Record{}}
}

func compositeKey(clientID, key string) string {
	return clientID + "\x00" + key
}

// PurgeExpired 清理 §2.3 的过期键，返回清理条数。
func (m *Middleware) PurgeExpired() int {
	n := 0
	for k, r := range m.Store {
		if r.ExpiresAt <= m.Clock {
			delete(m.Store, k)
			n++
		}
	}
	return n
}

func problem(status int, title, detail string) map[string]interface{} {
	return map[string]interface{}{
		"type":   "https://developer.example.com/idempotency",
		"status": status,
		"title":  title,
		"detail": detail,
	}
}

// Handle 走完整的幂等判定；handler 只在"首次"或"不要求键"时被调用。
func (m *Middleware) Handle(method, clientID string, headers map[string]string,
	payload map[string]interface{}, handler func() (int, interface{})) Response {

	switch strings.ToUpper(method) {
	case "GET", "HEAD", "OPTIONS", "TRACE", "PUT", "DELETE":
		s, b := handler()
		return Response{s, b, nil, "method already idempotent"}
	}

	raw, ok := headers["Idempotency-Key"]
	if !ok {
		if m.RequireKey {
			return Response{400, problem(400, "Idempotency-Key is missing",
				"This operation is idempotent and it requires correct usage of Idempotency Key."),
				map[string]string{"Link": DocLink}, "missing key"}
		}
		s, b := handler()
		return Response{s, b, nil, "no key required"}
	}

	key, ok := ParseStructuredString(raw)
	if !ok {
		return Response{400, problem(400, "Idempotency-Key is malformed",
			"The Idempotency-Key field value MUST be a String."),
			map[string]string{"Link": DocLink}, "not a structured string"}
	}
	if !IsValidKey(key) {
		return Response{400, problem(400, "Idempotency-Key is invalid",
			"The key does not match the published format."),
			map[string]string{"Link": DocLink}, "format rejected"}
	}

	fp := Fingerprint(payload)
	ck := compositeKey(clientID, key)
	rec, exists := m.Store[ck]
	if exists && rec.ExpiresAt <= m.Clock {
		delete(m.Store, ck)
		exists = false
	}
	if !exists {
		rec = &Record{Fingerprint: fp, State: "in_progress", ExpiresAt: m.Clock + m.TTL}
		m.Store[ck] = rec
		status, body := handler()
		rec.State = "done"
		rec.Status = status
		rec.Body = body
		return Response{status, body, nil, "first time"}
	}
	if rec.Fingerprint != fp {
		return Response{422, problem(422, "Idempotency-Key is already used",
			"Idempotency Key MUST not be reused across different payloads of this operation."),
			map[string]string{"Link": DocLink}, "fingerprint mismatch"}
	}
	if rec.State == "in_progress" {
		return Response{409, problem(409, "A request is outstanding for this Idempotency-Key",
			"A request with the same Idempotency-Key for the same operation is being processed."),
			map[string]string{"Link": DocLink}, "concurrent"}
	}
	return Response{rec.Status, rec.Body, nil, "replayed"}
}

func main() {
	fmt.Println("Idempotency-Key (draft-07) —— Go 版")
	const k = "8e03978e-40d5-43e8-bc93-6894a57f9324"
	quoted := `"` + k + `"`
	fmt.Println("带引号解析   :", mustParse(quoted))
	fmt.Println("不带引号解析 :", mustParse(k))

	seq := 0
	handler := func() (int, interface{}) {
		seq++
		return 201, map[string]interface{}{"id": seq, "item": 1}
	}
	mw := NewMiddleware(86400, true)
	h := map[string]string{"Idempotency-Key": quoted}
	payload := map[string]interface{}{"item": 1}
	fmt.Println("首次        :", mw.Handle("POST", "c1", h, payload, handler).Note)
	fmt.Println("重试        :", mw.Handle("POST", "c1", h, payload, handler).Note, "seq =", seq)
	fmt.Println("缺头        :", mw.Handle("POST", "c1", map[string]string{}, payload, handler).Status)
	fmt.Println("同键新payload:", mw.Handle("POST", "c1", h,
		map[string]interface{}{"item": 2}, handler).Status)
	fmt.Println("另一客户端  :", mw.Handle("POST", "c2", h, payload, handler).Note,
		"（复合键隔离，seq =", seq, "）")
	mw.Store[compositeKey("c1", k)].State = "in_progress"
	fmt.Println("并发重试    :", mw.Handle("POST", "c1", h, payload, handler).Status)
	fmt.Println("PUT 不需要键:", mw.Handle("PUT", "c1", map[string]string{}, payload, handler).Note)
}

func mustParse(s string) string {
	v, ok := ParseStructuredString(s)
	if !ok {
		return "<invalid>"
	}
	return v
}
