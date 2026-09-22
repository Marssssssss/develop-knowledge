// 610 WebSocket 控制帧 / Close 帧 / 状态码治理 —— Go 版（仅标准库）。
//
// 口径与 Python 版同源（RFC 6455 §5.5 §5.5.1 §7.4 + IANA Close Code 注册表）：
//   - 控制帧操作码最高位为 1；已定义 0x8/0x9/0xA，0xB-0xF 保留。
//   - 所有控制帧载荷 ≤ 125 字节且不得分片；可插在分片消息中间。
//   - Close body 前 2 字节是网络字节序状态码，其后可选 UTF-8 原因串。
//   - 1005/1006/1015 是保留值，端点 MUST NOT 写入。
//   - 0-999 不使用；1000-2999 本协议/扩展；3000-3999 库/框架/应用（可注册）；
//     4000-4999 私有（不可注册）。
//
// 关闭握手与心跳见 hb.go，重连退避见 backoff.go。
// 运行：go run ws.go hb.go backoff.go（本机无 Go 工具链，人工审查 + 结构校验）
package main

import (
	"encoding/binary"
	"fmt"
	"unicode/utf8"
)

// 操作码。
const (
	OpContinuation = 0x0
	OpText         = 0x1
	OpBinary       = 0x2
	OpClose        = 0x8
	OpPing         = 0x9
	OpPong         = 0xA
)

const ControlBit = 0x8
const MaxControlPayload = 125

// NeverSend §7.4.1 明说 "MUST NOT be set as a status code in a Close control
// frame" 的三个保留值。
var NeverSend = map[int]bool{1005: true, 1006: true, 1015: true}

// IsControl 控制帧的操作码最高位为 1。
func IsControl(op int) bool { return op&ControlBit != 0 }

// IsReservedData §5.6：0x3-0x7 保留给未来的非控制帧。
func IsReservedData(op int) bool { return op >= 0x3 && op <= 0x7 }

// IsReservedControl §5.5：0xB-0xF 保留给未来的控制帧。
func IsReservedControl(op int) bool { return op >= 0xB && op <= 0xF }

// ValidateControlFrame 三条硬约束：最高位为 1、载荷 ≤ 125、不得分片。
func ValidateControlFrame(op int, fin bool, payloadLen int) []string {
	out := []string{}
	if !IsControl(op) {
		out = append(out, fmt.Sprintf("操作码 0x%x 不是控制帧（最高位为 0）", op))
	}
	if IsReservedControl(op) {
		out = append(out, fmt.Sprintf("操作码 0x%x 属于保留的控制帧 0xB-0xF", op))
	}
	if payloadLen > MaxControlPayload {
		out = append(out, fmt.Sprintf("控制帧载荷 %d 字节，超过 125 字节上限", payloadLen))
	}
	if !fin {
		out = append(out, "控制帧不得分片（FIN 必须为 1）")
	}
	return out
}

// MayInterject §5.4：控制帧可以插在分片消息中间（前提是它自身合法）。
func MayInterject(op int, fin bool, payloadLen int) bool {
	return IsControl(op) && len(ValidateControlFrame(op, fin, payloadLen)) == 0
}

// EncodeCloseBody §5.5.1：前 2 字节网络字节序状态码 + 可选 UTF-8 原因串。
// hasCode 为 false 表示不携带状态码（body 必须为空）。
func EncodeCloseBody(code int, hasCode bool, reason string) ([]byte, []string) {
	if !hasCode {
		if reason != "" {
			return nil, []string{"不带状态码时不能携带原因串"}
		}
		return []byte{}, nil
	}
	if errs := StatusCodeIssues(code); len(errs) > 0 {
		return nil, errs
	}
	if !utf8.ValidString(reason) {
		return nil, []string{"原因串不是合法的 UTF-8"}
	}
	buf := make([]byte, 2)
	binary.BigEndian.PutUint16(buf, uint16(code))
	return append(buf, []byte(reason)...), nil
}

// ParseCloseBody 返回 (状态码, 是否带码, 原因串, 错误列表)。
// body 只有 1 字节是非法的：状态码必须占满 2 字节。
func ParseCloseBody(body []byte) (int, bool, string, []string) {
	if len(body) == 0 {
		return 0, false, "", nil
	}
	if len(body) == 1 {
		return 0, false, "", []string{"Close body 只有 1 字节，状态码必须占满 2 字节"}
	}
	code := int(binary.BigEndian.Uint16(body[:2]))
	tail := body[2:]
	if !utf8.Valid(tail) {
		return code, true, "", []string{"原因串不是合法的 UTF-8"}
	}
	return code, true, string(tail), nil
}

// StatusCodeIssues §7.4 的合法性。注意区分三类"不能用"：保留值（不得写入）、
// 1004（RFC 里就写着 Reserved）、1016-2999（区间归本协议但当前未分配）。
func StatusCodeIssues(code int) []string {
	out := []string{}
	switch {
	case code < 1000:
		out = append(out, fmt.Sprintf("状态码 %d 落在 0-999，该区间不使用", code))
	case NeverSend[code]:
		out = append(out, fmt.Sprintf("状态码 %d 是保留值，端点不得把它写入 Close 帧", code))
	case code == 1004:
		out = append(out, "状态码 1004 已保留，具体含义待定")
	case code >= 1016 && code <= 2999:
		out = append(out, fmt.Sprintf("状态码 %d 落在 1000-2999（本协议/扩展保留），当前未分配", code))
	case code >= 4000 && code <= 4999:
		out = append(out, fmt.Sprintf("状态码 %d 落在 4000-4999 私有区间，不可向 IANA 注册", code))
	case code >= 5000:
		out = append(out, fmt.Sprintf("状态码 %d 超出本协议定义的 0-4999 范围", code))
	}
	return out
}

// CodeRange §7.4.2 的四段区间归属。
func CodeRange(code int) string {
	switch {
	case code >= 0 && code <= 999:
		return "0-999 不使用"
	case code >= 1000 && code <= 2999:
		return "1000-2999 本协议/修订/扩展"
	case code >= 3000 && code <= 3999:
		return "3000-3999 库/框架/应用（向 IANA 注册）"
	case code >= 4000 && code <= 4999:
		return "4000-4999 私有用途（不可注册）"
	}
	return "超出 0-4999"
}

// IsRegisterable 只有 3000-3999 是"注册"语义；4000-4999 明说 can't be registered。
func IsRegisterable(code int) bool { return code >= 3000 && code <= 3999 }
