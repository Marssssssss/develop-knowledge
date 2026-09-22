// Package pushtype 复刻 APNs 的 apns-push-type 与 apns-topic 配套规则。
//
// 数据取自 Apple《Sending notification requests to APNs》的 "Know when to use
// push types" 定义列表（DocC JSON 原文，共 11 个取值）。
package pushtype

// 载荷上限。
const (
	// DefaultPayloadLimit 是非 VoIP 通知的 4 KB。
	DefaultPayloadLimit = 4096
	// VoIPPayloadLimit 是 VoIP 通知的 5 KB。
	VoIPPayloadLimit = 5120
	// DefaultPriority 是省略 apns-priority 时 APNs 采用的值。
	DefaultPriority = 10
)

// 推送证书里列出"允许哪些 push topic"的扩展 OID。
const (
	// CertExtWatchKit 是 WatchKit services 扩展。
	CertExtWatchKit = "1.2.840.113635.100.6.3.6"
	// CertExtVoIP 是 VoIP services 扩展。
	CertExtVoIP = "1.2.840.113635.100.6.3.4"
)

// Spec 是一个 apns-push-type 取值的全部约束。
// Suffix 为 "" 表示直接用 bundle ID；Priorities 为 nil 表示文档未约束；
// Auth 为 nil 表示 token/certificate 都行。
type Spec struct {
	Name         string
	Suffix       string
	SuffixDoc    string // 官方文档那张表的字面量，可能与 Suffix 不同
	Canonical    string // 别处有更权威写法时填这里，否则为 ""
	Priorities   []int
	Auth         []string
	Recommended  []string
	Unavailable  []string
	RequiredOn   []string
	CertExt      []string
	CertTopicSrc string
}

func set(items ...string) []string { return items }

// Catalog 是 11 个取值的完整登记表。
var Catalog = map[string]*Spec{
	"alert": {
		Name: "alert", Priorities: []int{10, 5},
		Recommended: set("macOS", "iOS", "tvOS", "iPadOS"),
		RequiredOn:  set("watchOS"),
	},
	"background": {
		Name: "background", Priorities: []int{5},
		Recommended: set("macOS", "iOS", "tvOS", "iPadOS"),
		RequiredOn:  set("watchOS"),
	},
	"complication": {
		Name: "complication", Suffix: ".complication",
		SuffixDoc: "h.complication", Canonical: ".complication",
		Recommended: set("watchOS", "iOS"),
		Unavailable: set("macOS", "tvOS", "iPadOS"),
		CertExt:     set(CertExtWatchKit),
	},
	"controls": {
		Name: "controls", Suffix: ".push-type.controls",
	},
	"fileprovider": {
		Name: "fileprovider", Suffix: ".pushkit.fileprovider",
		Recommended: set("macOS", "iOS", "tvOS", "iPadOS"),
		Unavailable: set("watchOS"),
	},
	"liveactivity": {
		Name: "liveactivity", Suffix: ".push-type.liveactivity",
		SuffixDoc: "push-type.liveactivity", Canonical: ".push-type.liveactivity",
		Recommended: set("iOS", "iPadOS"),
		Unavailable: set("watchOS", "macOS", "tvOS"),
	},
	"location": {
		Name: "location", Suffix: ".location-query",
		Priorities: []int{10, 5}, Auth: set("token"),
		Recommended: set("iOS", "iPadOS"),
		Unavailable: set("macOS", "tvOS", "watchOS"),
	},
	"mdm": {
		Name:         "mdm",
		Recommended:  set("macOS", "iOS", "tvOS", "iPadOS"),
		Unavailable:  set("watchOS"),
		CertTopicSrc: "UID attribute of the MDM push certificate subject",
	},
	"pushtotalk": {
		Name: "pushtotalk", Suffix: ".voip-ptt",
		Recommended: set("iOS", "iPadOS"),
		Unavailable: set("watchOS", "macOS", "tvOS"),
	},
	"voip": {
		Name: "voip", Suffix: ".voip",
		Recommended: set("macOS", "iOS", "tvOS", "iPadOS"),
		Unavailable: set("watchOS"),
		CertExt:     set(CertExtVoIP, CertExtWatchKit),
	},
	"widgets": {
		Name: "widgets", Suffix: ".push-type.widgets",
	},
}

// TopicFor 算出该 push type 应该用的 apns-topic。
// canonical 为 true 时，Canonical 字段非空就优先用它。
func TopicFor(name, bundleID, mdmTopic string, canonical bool) string {
	spec, ok := Catalog[name]
	if !ok {
		return ""
	}
	if spec.CertTopicSrc != "" {
		return mdmTopic
	}
	suffix := spec.Suffix
	if canonical && spec.Canonical != "" {
		suffix = spec.Canonical
	}
	return bundleID + suffix
}

// HasDocDiscrepancy 报告官方那张表的字面量与更权威写法是否不一致。
func HasDocDiscrepancy(name string) bool {
	spec, ok := Catalog[name]
	if !ok || spec.Canonical == "" {
		return false
	}
	return spec.Canonical != spec.SuffixDoc
}

// PayloadLimit 是该 push type 的载荷字节上限。
func PayloadLimit(name string) int {
	if name == "voip" {
		return VoIPPayloadLimit
	}
	return DefaultPayloadLimit
}

func contains(list []string, want string) bool {
	for _, v := range list {
		if v == want {
			return true
		}
	}
	return false
}

// Validate 返回这一组头部组合在文档语义下的问题；空切片表示没问题。
func Validate(name, topic string, priority int, platform, auth, bundleID string) []string {
	var problems []string
	spec, ok := Catalog[name]
	if !ok {
		return []string{"unknown apns-push-type: " + name}
	}
	if bundleID != "" && spec.CertTopicSrc == "" {
		if want := TopicFor(name, bundleID, "", true); topic != want {
			problems = append(problems, "topic mismatch: expected "+want)
		}
	}
	if platform != "" && contains(spec.Unavailable, platform) {
		problems = append(problems, name+" is not available on "+platform)
	}
	if priority > 0 && spec.Priorities != nil && !containsInt(spec.Priorities, priority) {
		problems = append(problems, "priority not allowed for "+name)
	}
	if spec.Auth != nil && !contains(spec.Auth, auth) {
		problems = append(problems, name+" supports only "+joinWith(spec.Auth, "/")+" authentication")
	}
	return problems
}

func containsInt(list []int, want int) bool {
	for _, v := range list {
		if v == want {
			return true
		}
	}
	return false
}

func joinWith(list []string, sep string) string {
	out := ""
	for i, v := range list {
		if i > 0 {
			out += sep
		}
		out += v
	}
	return out
}
