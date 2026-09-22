// Package serviceext 复刻 Notification Service App Extension 的处理流水线
// 与超时降级规则。
//
// 依据：Apple《Modifying content in newly delivered notifications》。
package serviceext

import "errors"

// 常量。
const (
	// MutableContentKey 是 aps 里的可变内容标记。
	MutableContentKey = "mutable-content"
	// EncryptedDataKey 是官方示例里放密文的自定义键。
	EncryptedDataKey = "ENCRYPTED_DATA"
	// Placeholder 是官方示例在解密失败/超时时使用的文案。
	Placeholder = "(Encrypted)"
	// DefaultBudget 是官方说的"about 30 seconds"。
	DefaultBudget = 30.0
)

// ErrHandlerCalledTwice 表示 completion handler 被调用了不止一次。
var ErrHandlerCalledTwice = errors.New("completion handler must be called exactly once")

// Request 是一条远程通知的投递请求。
type Request struct {
	APS           map[string]interface{}
	Custom        map[string]string
	AlertsEnabled bool
}

// SecretRequest 构造官方 Listing 2 那样的加密通知。
func SecretRequest(ciphertext, title, category, body string, alertsEnabled bool) *Request {
	alert := map[string]interface{}{"title": title, "body": body}
	aps := map[string]interface{}{
		"category":        category,
		MutableContentKey: 1,
		"alert":           alert,
	}
	return &Request{
		APS:           aps,
		Custom:        map[string]string{EncryptedDataKey: ciphertext},
		AlertsEnabled: alertsEnabled,
	}
}

// Alert 返回 aps.alert 归一化后的字典；字符串形式等价于 {"body": s}。
func (r *Request) Alert() map[string]string {
	out := map[string]string{}
	switch v := r.APS["alert"].(type) {
	case map[string]string:
		for k, val := range v {
			out[k] = val
		}
	case map[string]interface{}:
		for k, val := range v {
			if s, ok := val.(string); ok {
				out[k] = s
			}
		}
	case string:
		out["body"] = v
	}
	return out
}

// ExtensionEligible 报告扩展会不会被系统启用。
func ExtensionEligible(r *Request) (bool, string) {
	if !r.AlertsEnabled {
		return false, "alerts are disabled for your app"
	}
	switch v := r.APS[MutableContentKey].(type) {
	case bool:
		// JSON 的 true 不是数字 1
		return false, "mutable-content is not 1"
	case int:
		if v != 1 {
			return false, "mutable-content is not 1"
		}
	default:
		return false, "mutable-content is not 1"
	}
	alert := r.Alert()
	has := false
	for _, k := range []string{"title", "subtitle", "body"} {
		if _, ok := alert[k]; ok {
			has = true
		}
	}
	if !has {
		return false, "aps.alert has no title/subtitle/body"
	}
	return true, ""
}

// ServiceExtension 是一条通知在扩展里的生命周期。
type ServiceExtension struct {
	Budget              float64
	Decrypt             func(string) (string, error)
	HandlerCalled       bool
	FromTimeWillExpire  bool
	DeadlineNotified    bool
	Delivered           map[string]string
}

// NewServiceExtension 建立一条扩展处理记录。
func NewServiceExtension(budget float64, decrypt func(string) (string, error)) *ServiceExtension {
	return &ServiceExtension{Budget: budget, Decrypt: decrypt}
}

// Complete 就是 completion handler，只能调一次。
func (e *ServiceExtension) Complete(content map[string]string) (map[string]string, error) {
	if e.HandlerCalled {
		return nil, ErrHandlerCalledTwice
	}
	e.HandlerCalled = true
	e.Delivered = content
	return content, nil
}

// TimeWillExpire 是系统调用的 serviceExtensionTimeWillExpire()。
func (e *ServiceExtension) TimeWillExpire() map[string]string {
	if e.HandlerCalled {
		return e.Delivered
	}
	e.DeadlineNotified = true
	e.FromTimeWillExpire = true
	return nil
}

// Result 是一次 didReceive 的结果。
type Result struct {
	Employed            bool
	Reason              string
	Delivered           string
	Decrypted           bool
	TimedOut            bool
	FromTimeWillExpire  bool
}

// DidReceive 模拟 didReceive(_:withContentHandler:) 的主体。
func (e *ServiceExtension) DidReceive(r *Request, elapsed float64, decryptOK bool) Result {
	if ok, reason := ExtensionEligible(r); !ok {
		return Result{Employed: false, Reason: reason, Delivered: "original"}
	}
	content := map[string]string{}
	for k, v := range r.Alert() {
		content[k] = v
	}
	data, hasData := r.Custom[EncryptedDataKey]
	if !hasData {
		e.Complete(content)
		return Result{Employed: true, Delivered: "modified"}
	}
	if elapsed >= e.Budget {
		e.TimeWillExpire()
		content["subtitle"] = Placeholder
		content["body"] = ""
		e.Complete(content)
		return Result{Employed: true, Delivered: "modified",
			TimedOut: true, FromTimeWillExpire: true}
	}
	if decryptOK && e.Decrypt != nil {
		if plain, err := e.Decrypt(data); err == nil {
			content["body"] = plain
			e.Complete(content)
			return Result{Employed: true, Delivered: "modified", Decrypted: true}
		}
	}
	content["body"] = Placeholder
	e.Complete(content)
	return Result{Employed: true, Delivered: "modified"}
}

// Outcome 是预算耗尽后的最终处置：没调过 handler 就展示原始内容。
func (e *ServiceExtension) Outcome(r *Request) (string, map[string]string) {
	if e.HandlerCalled {
		return "modified", e.Delivered
	}
	return "original", r.Alert()
}
