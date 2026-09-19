// Richardson 成熟度模型 + RFC 9110 §9.2 方法语义 —— Go 版对照实现（仅标准库）。
//
// 口径来源：
//   - Martin Fowler, "Richardson Maturity Model: steps toward the glory of REST"（2010-03-18）
//     <https://martinfowler.com/articles/richardsonMaturityModel.html> 全文实读：
//     Level 0 把 HTTP 当隧道；Level 1 引入资源（分而治之）；Level 2 引入动词与状态码
//     （消除不必要的变化）；Level 3 引入超媒体控制（discoverability）。文中明说
//     "level 3 RMM is a pre-condition of REST"，且该模型"should not be used in some kind of
//     assessment mechanism"。
//   - RFC 9110 §9.2（实读 rfc-editor.org/rfc/rfc9110.txt）：
//     §9.2.1 safe = GET/HEAD/OPTIONS/TRACE；资源所有者 MUST 禁用 safe 方法上的不安全动作
//     （"page?do=delete" 反例）；§9.2.2 idempotent = PUT/DELETE + 全部 safe 方法，
//     "A proxy MUST NOT automatically retry non-idempotent requests"；
//     §9.2.3 只给 GET/HEAD/POST 定义了缓存语义，而"overwhelming majority"的实现只支持 GET/HEAD。
//
// 运行：go run maturity.go（本机无 Go 工具链，代码经人工审查 + 结构校验）
package main

import (
	"fmt"
	"sort"
	"strings"
)

// SafeMethods 是 §9.2.1 定义的 safe 方法集合。
var SafeMethods = []string{"GET", "HEAD", "OPTIONS", "TRACE"}

// IdempotentMethods 是 §9.2.2：PUT、DELETE 与全部 safe 方法。
var IdempotentMethods = []string{"GET", "HEAD", "OPTIONS", "TRACE", "PUT", "DELETE"}

// CacheSemanticsDefined 是 §9.2.3：规范只为这三个方法定义了缓存语义。
var CacheSemanticsDefined = []string{"GET", "HEAD", "POST"}

func inList(list []string, m string) bool {
	m = strings.ToUpper(m)
	for _, x := range list {
		if x == m {
			return true
		}
	}
	return false
}

// IsSafe 判断方法是否只读语义。
func IsSafe(m string) bool { return inList(SafeMethods, m) }

// IsIdempotent 判断方法是否幂等。
func IsIdempotent(m string) bool { return inList(IdempotentMethods, m) }

// HasCacheSemantics 判断规范是否为该方法定义了缓存语义。
func HasCacheSemantics(m string) bool { return inList(CacheSemanticsDefined, m) }

// ClientMayAutoRetry 复刻 §9.2.2 的 SHOULD NOT + 两个例外。
func ClientMayAutoRetry(m string, knowsIdempotent, canDetectNeverApplied bool) bool {
	if IsIdempotent(m) {
		return true
	}
	return knowsIdempotent || canDetectNeverApplied
}

// ProxyMayAutoRetry 是 §9.2.2 的 MUST NOT：代理只能自动重试幂等请求。
func ProxyMayAutoRetry(m string) bool { return IsIdempotent(m) }

// SafeMethodViolation 复刻 §9.2.1：safe 方法上挂不安全动作即违规。
func SafeMethodViolation(m string, actionUnsafe bool) bool {
	return IsSafe(m) && actionUnsafe
}

// Exchange 是一次请求/响应对的可观测特征。
type Exchange struct {
	Method     string
	Path       string
	Status     int
	Location   string
	Links      []string
	HasResIDs  bool
}

func paths(ex []Exchange) []string {
	out := make([]string, 0, len(ex))
	for _, e := range ex {
		out = append(out, strings.SplitN(e.Path, "?", 2)[0])
	}
	return out
}

// UsesResources 判 Level 1：请求不再打同一个端点。
func UsesResources(ex []Exchange) bool {
	seen := map[string]bool{}
	for _, p := range paths(ex) {
		seen[p] = true
	}
	return len(seen) > 1
}

// UsesHTTPVerbs 判 Level 2 的一半：读取用了 safe 方法而非 POST 隧道。
func UsesHTTPVerbs(ex []Exchange) bool {
	for _, e := range ex {
		if IsSafe(e.Method) {
			return true
		}
	}
	return false
}

// UsesStatusCodes 判 Level 2 的另一半：用状态码表达结果而非一律 200。
func UsesStatusCodes(ex []Exchange) bool {
	for _, e := range ex {
		if e.Status != 200 {
			return true
		}
	}
	return false
}

// UsesHypermedia 判 Level 3：响应带超媒体控制。
func UsesHypermedia(ex []Exchange) bool {
	for _, e := range ex {
		if len(e.Links) > 0 {
			return true
		}
	}
	return false
}

// RichardsonLevel 从报文反推 RMM 等级（0..3），逐级短路。
func RichardsonLevel(ex []Exchange) int {
	if !UsesResources(ex) {
		return 0
	}
	if !UsesHTTPVerbs(ex) || !UsesStatusCodes(ex) {
		return 1
	}
	if !UsesHypermedia(ex) {
		return 2
	}
	return 3
}

// LevelMeaning 对应 Fowler 文末 Ian Robinson 的总结。
var LevelMeaning = map[int]string{
	0: "把 HTTP 当隧道（RPC/POX），所有请求打同一个端点",
	1: "引入资源：把大端点拆成多个可寻址资源（分而治之）",
	2: "引入标准动词与状态码：同类情况用同样方式处理（消除不必要的变化）",
	3: "引入超媒体控制：协议自描述、可发现（discoverability）",
}

func level0() []Exchange {
	e := Exchange{Method: "POST", Path: "/appointmentService", Status: 200}
	return []Exchange{e, e, e}
}

func level1() []Exchange {
	return []Exchange{
		{Method: "POST", Path: "/doctors/mjones", Status: 200, HasResIDs: true},
		{Method: "POST", Path: "/slots/1234", Status: 200, HasResIDs: true},
	}
}

func level2() []Exchange {
	return []Exchange{
		{Method: "GET", Path: "/doctors/mjones/slots?date=20100104&status=open", Status: 200, HasResIDs: true},
		{Method: "POST", Path: "/slots/1234", Status: 201, Location: "/slots/1234/appointment"},
		{Method: "POST", Path: "/slots/1234", Status: 409},
	}
}

func level3() []Exchange {
	return []Exchange{
		{Method: "GET", Path: "/doctors/mjones/slots?date=20100104&status=open", Status: 200,
			Links: []string{"/linkrels/slot/book"}, HasResIDs: true},
		{Method: "POST", Path: "/slots/1234", Status: 201, Location: "/slots/1234/appointment",
			Links: []string{"/linkrels/appointment/cancel", "/linkrels/appointment/addTest",
				"self", "/linkrels/appointment/changeTime",
				"/linkrels/appointment/updateContactInfo", "/linkrels/help"}},
		{Method: "POST", Path: "/slots/1234", Status: 409, Links: []string{"/linkrels/slot/book"}},
	}
}

func methodsOf(ex []Exchange) []string {
	seen := map[string]bool{}
	for _, e := range ex {
		seen[e.Method] = true
	}
	out := make([]string, 0, len(seen))
	for k := range seen {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func main() {
	fmt.Println("RMM + RFC 9110 §9.2 —— Go 版")
	fmt.Println("safe      :", SafeMethods)
	fmt.Println("idempotent:", IdempotentMethods)
	fmt.Println("POST 客户端自动重试  :", ClientMayAutoRetry("POST", false, false))
	fmt.Println("POST 已知语义幂等    :", ClientMayAutoRetry("POST", true, false))
	fmt.Println("POST 代理自动重试    :", ProxyMayAutoRetry("POST"))
	fmt.Println("GET  page?do=delete  :", SafeMethodViolation("GET", true))

	flows := map[string][]Exchange{"L0": level0(), "L1": level1(), "L2": level2(), "L3": level3()}
	names := []string{"L0", "L1", "L2", "L3"}
	for _, n := range names {
		lv := RichardsonLevel(flows[n])
		fmt.Printf("%s → level %d  methods=%v  %s\n", n, lv, methodsOf(flows[n]), LevelMeaning[lv])
	}
}
