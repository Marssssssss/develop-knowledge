// Package fcmtopic 复刻 FCM 的主题名规范化、主题管理请求/响应、TTL 编码
// 与 send_each 的批量扇出模型。
//
// 语义依据是 Google 官方仓库 firebase/firebase-admin-python 的
// firebase_admin/messaging.py、_messaging_encoder.py、_messaging_utils.py
//（本轮 firebase.google.com 不可达，已在 README 标注口径）。
package fcmtopic

import (
	"fmt"
	"math"
	"regexp"
	"strings"
	"time"
)

// 端点与常量。
const (
	// IIDURL 是实例 ID（主题管理）服务地址。
	IIDURL = "https://iid.googleapis.com"
	// TopicPrefix 是主题名前缀。
	TopicPrefix = "/topics/"
	// SendEachLimit 是 send_each 一次能带的消息条数上限。
	SendEachLimit = 500
	// MaxActiveCollapseKeys 来自官方 docstring：同时最多 4 个折叠键。
	MaxActiveCollapseKeys = 4
	// OperationSubscribe 是订阅操作路径。
	OperationSubscribe = "iid/v1:batchAdd"
	// OperationUnsubscribe 是退订操作路径。
	OperationUnsubscribe = "iid/v1:batchRemove"
)

// TopicNameRE 与官方 SDK 完全一致。注意 `9-_` 里的 '-' 是**字面量**而不是
// 区间起点，实测 0x3a~0x40（: ; < = > ? @）都不匹配。
var TopicNameRE = regexp.MustCompile(`^[a-zA-Z0-9-_\.~%]+$`)

// FCMErrorTypes 是官方 SDK 的错误码到异常类型的映射表。
var FCMErrorTypes = map[string]string{
	"APNS_AUTH_ERROR":        "ThirdPartyAuthError",
	"QUOTA_EXCEEDED":         "QuotaExceededError",
	"SENDER_ID_MISMATCH":     "SenderIdMismatchError",
	"THIRD_PARTY_AUTH_ERROR": "ThirdPartyAuthError",
	"UNREGISTERED":           "UnregisteredError",
}

// ErrorInfo 是主题管理响应里的一个失败项。
type ErrorInfo struct {
	Index  int
	Reason string
}

// TopicManagementResponse 按 results 里有没有 error 逐条计数。
type TopicManagementResponse struct {
	SuccessCount int
	FailureCount int
	Errors       []ErrorInfo
}

// NewTopicManagementResponse 解析 IID 的响应体。
func NewTopicManagementResponse(results []map[string]string) (*TopicManagementResponse, error) {
	if results == nil {
		return nil, fmt.Errorf("unexpected topic management response")
	}
	resp := &TopicManagementResponse{}
	for i, r := range results {
		if reason, ok := r["error"]; ok {
			resp.FailureCount++
			resp.Errors = append(resp.Errors, ErrorInfo{Index: i, Reason: reason})
		} else {
			resp.SuccessCount++
		}
	}
	return resp, nil
}

// SanitizeTopicName 剥掉 /topics/ 前缀并校验字符集；空值返回 ""。
func SanitizeTopicName(topic string) (string, error) {
	if topic == "" {
		return "", nil
	}
	if strings.HasPrefix(topic, TopicPrefix) {
		topic = topic[len(TopicPrefix):]
	}
	if !TopicNameRE.MatchString(topic) {
		return "", fmt.Errorf("malformed topic name")
	}
	return topic, nil
}

// TopicRequest 是一次主题管理操作要发的请求。
type TopicRequest struct {
	URL     string
	Headers map[string]string
	To      string
	Tokens  []string
}

// BuildTopicRequest 构造订阅/退订请求。
func BuildTopicRequest(tokens []string, topic, operation string) (*TopicRequest, error) {
	if len(tokens) == 0 {
		return nil, fmt.Errorf("tokens must be a string or a non-empty list of strings")
	}
	for _, t := range tokens {
		if t == "" {
			return nil, fmt.Errorf("tokens must be non-empty strings")
		}
	}
	if topic == "" {
		return nil, fmt.Errorf("topic must be a non-empty string")
	}
	if operation != OperationSubscribe && operation != OperationUnsubscribe {
		return nil, fmt.Errorf("unknown topic management operation")
	}
	if !strings.HasPrefix(topic, TopicPrefix) {
		topic = TopicPrefix + topic
	}
	return &TopicRequest{
		URL:     IIDURL + "/" + operation,
		Headers: map[string]string{"access_token_auth": "true"},
		To:      topic,
		Tokens:  tokens,
	}, nil
}

// EncodeTTL 按官方 encode_ttl 的规则编码成 "3s" / "3.500000000s"。
func EncodeTTL(d time.Duration) string {
	if d < 0 {
		panic("AndroidConfig.ttl must not be negative")
	}
	total := d.Seconds()
	seconds := int64(math.Floor(total))
	nanos := int64((total - float64(seconds)) * 1e9)
	if nanos != 0 {
		return fmt.Sprintf("%d.%09ds", seconds, nanos)
	}
	return fmt.Sprintf("%ds", seconds)
}

// SendResponse 是单条消息的发送结果。
// Success 的官方定义是 "message_id 非空且没有异常"——没报错不等于成功。
type SendResponse struct {
	MessageID string
	Exception error
}

// Success 报告这条消息是否发送成功。
func (r *SendResponse) Success() bool {
	return r.MessageID != "" && r.Exception == nil
}

// BatchResponse 聚合一批 SendResponse。
type BatchResponse struct {
	Responses []SendResponse
}

// SuccessCount 统计成功的条数。
func (b *BatchResponse) SuccessCount() int {
	n := 0
	for i := range b.Responses {
		if b.Responses[i].Success() {
			n++
		}
	}
	return n
}

// FailureCount 统计失败的条数。
func (b *BatchResponse) FailureCount() int {
	return len(b.Responses) - b.SuccessCount()
}

// PlanSendEach 返回 send_each 的执行计划；不合规直接返回错误。
func PlanSendEach(messages []map[string]string) (int, error) {
	if len(messages) > SendEachLimit {
		return 0, fmt.Errorf("messages must not contain more than 500 elements")
	}
	if len(messages) == 0 {
		// 官方实现把 max_workers 设成 len(messages)，0 会被标准库拒绝
		return 0, fmt.Errorf("max_workers must be greater than 0")
	}
	return len(messages), nil
}

// ClassifyFCMError 把错误码映射成异常类型名；未登记返回 ""。
func ClassifyFCMError(code string) string {
	return FCMErrorTypes[code]
}
