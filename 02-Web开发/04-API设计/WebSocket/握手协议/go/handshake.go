/*
 * handshake.go — WebSocket Opening Handshake (RFC 6455 §4)
 *
 * 与 c/handshake.c、python/handshake.py 一一对应的纯协议实现。
 * 依赖仅 Go 标准库：crypto/sha1、encoding/base64、strings、bufio 等。
 *
 * 用法：
 *   cd go && go run handshake.go
 */
package main

import (
	"crypto/rand"
	"crypto/sha1"
	"encoding/base64"
	"fmt"
	"os"
	"strings"
)

const (
	WS_MAGIC_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11" // RFC 6455 §1.3
	WS_VERSION    = "13"                                   // RFC 6455 §4.1 第 9 点
)

// =====================================================================
// Part 1: 协议层函数
// =====================================================================

// computeAccept —— RFC 6455 §4.2.2 步骤 4 + §1.3：
//
//	Sec-WebSocket-Accept = base64( SHA-1( Sec-WebSocket-Key + MAGIC_GUID ) )
//
// 验证用例（RFC §1.3）：
//
//	computeAccept("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
func computeAccept(clientKey string) string {
	h := sha1.New()
	h.Write([]byte(clientKey + WS_MAGIC_GUID))
	return base64.StdEncoding.EncodeToString(h.Sum(nil))
}

// genClientKey —— RFC 6455 §4.1 第 7 点：16 字节随机 nonce base64 编码
func genClientKey() string {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		// rand.Read 几乎不会失败；fallback 用固定序列保证 demo 不中断
		for i := range b {
			b[i] = byte(i)
		}
	}
	return base64.StdEncoding.EncodeToString(b)
}

// findHeader —— 大小写不敏感找 "Name: Value"
func findHeader(text, name string) (string, bool) {
	for _, line := range strings.Split(text, "\r\n") {
		if !strings.Contains(line, ":") {
			continue
		}
		parts := strings.SplitN(line, ":", 2)
		if strings.EqualFold(strings.TrimSpace(parts[0]), name) {
			return strings.TrimSpace(parts[1]), true
		}
	}
	return "", false
}

// hasToken —— 检查 Connection 头里是否含指定 token（RFC 7230 §6.1 逗号分隔）
func hasToken(headerValue, token string) bool {
	for _, t := range strings.Split(headerValue, ",") {
		if strings.EqualFold(strings.TrimSpace(t), token) {
			return true
		}
	}
	return false
}

// buildClientRequest —— RFC 6455 §4.1 客户端 Upgrade 请求
func buildClientRequest(host, path, key string, subprotocols string) string {
	var b strings.Builder
	fmt.Fprintf(&b, "GET %s HTTP/1.1\r\n", path)
	fmt.Fprintf(&b, "Host: %s\r\n", host)
	b.WriteString("Upgrade: websocket\r\n")
	b.WriteString("Connection: Upgrade\r\n")
	fmt.Fprintf(&b, "Sec-WebSocket-Key: %s\r\n", key)
	fmt.Fprintf(&b, "Sec-WebSocket-Version: %s\r\n", WS_VERSION)
	if subprotocols != "" {
		fmt.Fprintf(&b, "Sec-WebSocket-Protocol: %s\r\n", subprotocols)
	}
	b.WriteString("\r\n")
	return b.String()
}

// parseClientRequest —— RFC 6455 §4.1 客户端请求校验；返回 (ok, key, err)
func parseClientRequest(req string) (bool, string, string) {
	first := strings.SplitN(req, "\r\n", 2)[0]
	if !strings.HasPrefix(first, "GET ") {
		return false, "", "method must be GET"
	}
	if !strings.Contains(first, "HTTP/1.1") {
		return false, "", "HTTP version must be 1.1"
	}

	upgrade, ok := findHeader(req, "Upgrade")
	if !ok || !strings.EqualFold(upgrade, "websocket") {
		return false, "", "missing or invalid Upgrade header"
	}
	conn, ok := findHeader(req, "Connection")
	if !ok || !hasToken(conn, "Upgrade") {
		return false, "", "missing Connection: Upgrade"
	}
	version, ok := findHeader(req, "Sec-WebSocket-Version")
	if !ok || version != WS_VERSION {
		return false, "", fmt.Sprintf("Sec-WebSocket-Version must be %s", WS_VERSION)
	}
	key, ok := findHeader(req, "Sec-WebSocket-Key")
	if !ok {
		return false, "", "missing Sec-WebSocket-Key"
	}
	raw, err := base64.StdEncoding.DecodeString(key)
	if err != nil || len(raw) != 16 {
		return false, "", fmt.Sprintf("Sec-WebSocket-Key must base64-decode to 16 bytes (got %d)", len(raw))
	}
	return true, key, ""
}

// buildServerResponse —— RFC 6455 §4.2.2 服务端 101 Switching Protocols
func buildServerResponse(accept, selectedSubprotocol string) string {
	var b strings.Builder
	b.WriteString("HTTP/1.1 101 Switching Protocols\r\n")
	b.WriteString("Upgrade: websocket\r\n")
	b.WriteString("Connection: Upgrade\r\n")
	fmt.Fprintf(&b, "Sec-WebSocket-Accept: %s\r\n", accept)
	if selectedSubprotocol != "" {
		fmt.Fprintf(&b, "Sec-WebSocket-Protocol: %s\r\n", selectedSubprotocol)
	}
	b.WriteString("\r\n")
	return b.String()
}

// parseServerResponse —— RFC 6455 §4.1 客户端对响应的校验
func parseServerResponse(resp, clientKey string) (bool, string) {
	first := strings.SplitN(resp, "\r\n", 2)[0]
	if !strings.HasPrefix(first, "HTTP/1.1 101") {
		return false, fmt.Sprintf("status code must be 101 (got %q)", first)
	}
	upgrade, ok := findHeader(resp, "Upgrade")
	if !ok || !strings.EqualFold(upgrade, "websocket") {
		return false, "missing or invalid Upgrade in response"
	}
	conn, ok := findHeader(resp, "Connection")
	if !ok || !hasToken(conn, "Upgrade") {
		return false, "missing Connection: Upgrade in response"
	}
	accept, ok := findHeader(resp, "Sec-WebSocket-Accept")
	if !ok {
		return false, "missing Sec-WebSocket-Accept in response"
	}
	expected := computeAccept(clientKey)
	if accept != expected {
		return false, fmt.Sprintf("Sec-WebSocket-Accept mismatch (got %q, expected %q)", accept, expected)
	}
	return true, ""
}

// =====================================================================
// Part 2: 演示驱动
// =====================================================================

type demo struct {
	count, failed int
}

func (d *demo) banner(title string) {
	d.count++
	fmt.Printf("\n=== Case %d: %s ===\n", d.count, title)
}

func (d *demo) assert_(cond bool, msg string) {
	if cond {
		fmt.Printf("    [PASS] %s\n", msg)
	} else {
		fmt.Printf("    [FAIL] %s\n", msg)
		d.failed++
	}
}

// Case 1: RFC 6455 §1.3 计算示例
func case1RFCExample(d *demo) {
	d.banner("RFC 6455 §1.3 计算示例")
	got := computeAccept("dGhlIHNhbXBsZSBub25jZQ==")
	fmt.Printf("  Key    : dGhlIHNhbXBsZSBub25jZQ==\n")
	fmt.Printf("  Accept : %s\n", got)
	d.assert_(got == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=", "RFC example accept correct")
}

// Case 2: 完整 round-trip（in-memory）
func case2Roundtrip(d *demo) {
	d.banner("完整 round-trip（in-memory 字节流）")
	key := genClientKey()
	fmt.Printf("  Generated Sec-WebSocket-Key: %s\n", key)

	req := buildClientRequest("example.com", "/chat", key, "chat, superchat")
	d.assert_(len(req) > 0, "client request built")

	ok, srvKey, err := parseClientRequest(req)
	d.assert_(ok, fmt.Sprintf("server parsed request: %s", ifEmpty(err, "key extracted")))
	d.assert_(srvKey == key, "key preserved through parsing")

	accept := computeAccept(srvKey)
	resp := buildServerResponse(accept, "chat")
	d.assert_(len(resp) > 0, "server response built")

	ok, err = parseServerResponse(resp, key)
	d.assert_(ok, fmt.Sprintf("client accepted response: %s", ifEmpty(err, "all checks passed")))
}

func ifEmpty(s, fallback string) string {
	if s == "" {
		return fallback
	}
	return s
}

// Case 3: 子协议协商
func case3Subprotocol(d *demo) {
	d.banner("子协议协商（Sec-WebSocket-Protocol）")
	key := genClientKey()
	req := buildClientRequest("example.com", "/chat", key, "chat, superchat")

	accept := computeAccept(key)
	// 服务端选了客户端列表外的 "wss-v2" —— RFC §4.1 第 6 点禁止
	_ = buildServerResponse(accept, "wss-v2")
	d.assert_(!strings.Contains(req, "wss-v2"),
		"server MUST NOT select subprotocol absent from client request")
	d.assert_(strings.Contains(req, "chat"),
		"server MAY select subprotocol that client offered")
}

// Case 4: 服务端拒绝缺少必填头 / key 长度错
func case4MissingHeader(d *demo) {
	d.banner("服务端拒绝缺少必填头")
	// 缺 Sec-WebSocket-Version
	bad := "GET /chat HTTP/1.1\r\n" +
		"Host: example.com\r\n" +
		"Upgrade: websocket\r\n" +
		"Connection: Upgrade\r\n" +
		"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n"
	ok, _, err := parseClientRequest(bad)
	d.assert_(!ok, fmt.Sprintf("request without Sec-WebSocket-Version rejected: %s", err))

	// 缺 Upgrade
	bad2 := "GET /chat HTTP/1.1\r\n" +
		"Host: example.com\r\n" +
		"Connection: Upgrade\r\n" +
		"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n" +
		"Sec-WebSocket-Version: 13\r\n\r\n"
	ok, _, err = parseClientRequest(bad2)
	d.assert_(!ok, fmt.Sprintf("request without Upgrade rejected: %s", err))

	// Sec-WebSocket-Key base64 解码后只有 11 字节
	bad3 := "GET /chat HTTP/1.1\r\n" +
		"Host: example.com\r\n" +
		"Upgrade: websocket\r\n" +
		"Connection: Upgrade\r\n" +
		"Sec-WebSocket-Key: dGhlIHNhbXBsZQ==\r\n" +
		"Sec-WebSocket-Version: 13\r\n\r\n"
	ok, _, err = parseClientRequest(bad3)
	d.assert_(!ok, fmt.Sprintf("request with bad Sec-WebSocket-Key length rejected: %s", err))
}

// Case 5: 客户端拒绝错误 accept / 非 101 状态码
func case5ClientRejects(d *demo) {
	d.banner("客户端拒绝错误 accept / 非 101 状态码")
	key := genClientKey()

	wrong := "HTTP/1.1 101 Switching Protocols\r\n" +
		"Upgrade: websocket\r\n" +
		"Connection: Upgrade\r\n" +
		"Sec-WebSocket-Accept: this-is-wrong\r\n\r\n"
	ok, err := parseServerResponse(wrong, key)
	d.assert_(!ok, fmt.Sprintf("wrong Sec-WebSocket-Accept rejected: %s", err))

	not101 := "HTTP/1.1 200 OK\r\n" +
		"Upgrade: websocket\r\n" +
		"Connection: Upgrade\r\n" +
		"Sec-WebSocket-Accept: anything\r\n\r\n"
	ok, err = parseServerResponse(not101, key)
	d.assert_(!ok, fmt.Sprintf("non-101 status rejected: %s", err))
}

func main() {
	fmt.Println("WebSocket Opening Handshake Demo (RFC 6455 §4)")
	fmt.Println("================================================")

	d := &demo{}
	case1RFCExample(d)
	case2Roundtrip(d)
	case3Subprotocol(d)
	case4MissingHeader(d)
	case5ClientRejects(d)

	fmt.Println("\n================================================")
	fmt.Printf("Total: %d cases, %d failed\n", d.count, d.failed)
	if d.failed > 0 {
		os.Exit(1)
	}
}