package main

import "strings"

func isHex(s string) bool {
	if s == "" {
		return false
	}
	for i := 0; i < len(s); i++ {
		c := s[i]
		if !((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F')) {
			return false
		}
	}
	return true
}

func hexVal(s string) int {
	n := 0
	for i := 0; i < len(s); i++ {
		c := s[i]
		var d int
		switch {
		case c >= '0' && c <= '9':
			d = int(c - '0')
		case c >= 'a' && c <= 'f':
			d = int(c-'a') + 10
		default:
			d = int(c-'A') + 10
		}
		n = n*16 + d
	}
	return n
}

// DecodeChunked 按 §7.1.3 解码 chunked，返回 body、下一位置与错误说明。
func DecodeChunked(buf string, pos int) (string, int, string) {
	out := ""
	for {
		i := strings.Index(buf[pos:], "\r\n")
		if i < 0 {
			return "", pos, "truncated: chunk-size 后无 CRLF"
		}
		i += pos
		line := buf[pos:i]
		sizeTok := line
		if semi := strings.Index(line, ";"); semi >= 0 {
			sizeTok = line[:semi]
		}
		sizeTok = strings.TrimSpace(sizeTok)
		if !isHex(sizeTok) {
			return "", pos, "bad chunk-size " + sizeTok
		}
		size := hexVal(sizeTok)
		pos = i + 2
		if size == 0 {
			// trailer-section：*( field-line CRLF ) CRLF
			for {
				j := strings.Index(buf[pos:], "\r\n")
				if j < 0 {
					return "", pos, "truncated: trailer 未终止"
				}
				j += pos
				if j == pos {
					return out, pos + 2, ""
				}
				pos = j + 2
			}
		}
		if len(buf) < pos+size+2 {
			return "", pos, "truncated: chunk-data 不足"
		}
		out += buf[pos : pos+size]
		pos += size
		if buf[pos:pos+2] != "\r\n" {
			return "", pos, "chunk-data 后不是 CRLF"
		}
		pos += 2
	}
}

// Request 是一个解析出的请求。
type Request struct {
	Method  string
	Target  string
	Headers Headers
	Body    string
}

// Parser 是一个接收方。Policy 为 "cl" 时 CL 优先，为 "te" 时 TE 优先。
type Parser struct {
	Name   string
	Policy string
}

// choose 决定用哪种方式取 body。
func (p Parser) choose(h Headers) (string, int) {
	teRaw, hasTE := h.Get("Transfer-Encoding")
	clRaw, hasCL := h.Get("Content-Length")
	var codings []string
	if hasTE {
		codings = ParseTE(teRaw)
	}
	chunkedOK := len(codings) > 0 && codings[len(codings)-1] == "chunked"
	if p.Policy == "te" {
		if chunkedOK {
			return "chunked", 0
		}
		if hasCL {
			if n, ok := ParseCL(clRaw); ok {
				return "length", n
			}
			return "length", -1
		}
		return "none", 0
	}
	if hasCL {
		if n, ok := ParseCL(clRaw); ok {
			return "length", n
		}
	}
	if chunkedOK {
		return "chunked", 0
	}
	return "none", 0
}

// ParseOne 从 buf[pos:] 读一个请求。
func (p Parser) ParseOne(buf string, pos int) (Request, int, string) {
	end := strings.Index(buf[pos:], "\r\n\r\n")
	if end < 0 {
		return Request{}, pos, p.Name + ": 头部未终止"
	}
	end += pos
	head := buf[pos:end]
	bodyAt := end + 4
	lines := strings.Split(head, "\r\n")
	parts := strings.Split(lines[0], " ")
	if len(parts) != 3 {
		return Request{}, pos, p.Name + ": 请求行不合法"
	}
	var pairs [][2]string
	for _, ln := range lines[1:] {
		k, v := ln, ""
		if i := strings.Index(ln, ":"); i >= 0 {
			k, v = ln[:i], ln[i+1:]
		}
		pairs = append(pairs, [2]string{strings.TrimSpace(k), strings.TrimSpace(v)})
	}
	h := Headers{Pairs: pairs}

	kind, arg := p.choose(h)
	switch kind {
	case "none":
		return Request{parts[0], parts[1], h, ""}, bodyAt, ""
	case "length":
		if arg < 0 {
			return Request{}, pos, p.Name + ": Content-Length 非法"
		}
		if len(buf) < bodyAt+arg {
			return Request{}, pos, p.Name + ": body 不足"
		}
		return Request{parts[0], parts[1], h, buf[bodyAt : bodyAt+arg]}, bodyAt + arg, ""
	}
	body, nxt, err := DecodeChunked(buf, bodyAt)
	if err != "" {
		return Request{}, pos, p.Name + ": " + err
	}
	return Request{parts[0], parts[1], h, body}, nxt, ""
}

// HopResult 是两跳解析的结果。
type HopResult struct {
	Front    Request
	FrontEnd int
	Back     Request
	BackEnd  int
	Leftover string
	Err      string
}

// TwoHop 前端读一个请求后把「它认为的完整请求」转发给后端。
// Leftover 是后端连接上的残留字节，即被走私的前缀。
func TwoHop(buf string, front, back Parser) HopResult {
	r1, p1, err := front.ParseOne(buf, 0)
	if err != "" {
		return HopResult{Err: err}
	}
	forwarded := buf[:p1]
	r2, p2, err := back.ParseOne(forwarded, 0)
	if err != "" {
		return HopResult{Front: r1, FrontEnd: p1, Err: err}
	}
	return HopResult{r1, p1, r2, p2, forwarded[p2:], ""}
}
