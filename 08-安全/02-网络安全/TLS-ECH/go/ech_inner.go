// ech_inner.go —— EncodedClientHelloInner 的构造与还原（RFC 9849 §5.1、§6.1.3）
//
// EncodedClientHelloInner = ClientHelloInner（legacy_session_id 清空）| 全零填充。
// 填充的目的不是隐蔽长度，而是**把长度对齐到 32 字节**：有 SNI 时补
// max(0, M - D)（M = maximum_name_length、D = SNI 长度），无 SNI 时补 M + 9，
// 最后再补到 32 的倍数。被动观察者因此无法从长度区分不同的 SNI。
//
// ech_outer_extensions(0xfd00) 压缩：内层里与 ClientHelloOuter 逐字节相同的扩展不
// 重复发送，改成一个类型列表，服务端按列表从 outer 里搬回来。这里集中了 §5.1 的
// 四条 MUST abort —— 少了任何一条，服务端就成了放大攻击的反射器。
package main

import (
	"bytes"
	"errors"
)

var (
	errEchPadding      = errors.New("ech: 尾部填充含非零字节")
	errOuterExtMissing = errors.New("ech: abort 1 - 引用了 ClientHelloOuter 里没有的扩展")
	errOuterExtDupe    = errors.New("ech: abort 2 - 重复引用同一扩展")
	errOuterExtSelf    = errors.New("ech: abort 3 - 引用了 encrypted_client_hello 自己")
	errOuterExtOrder   = errors.New("ech: abort 4 - 相对顺序与 ClientHelloOuter 不一致")
)

// namePadding —— §6.1.3 第 1 步：返回要补的字节数
func namePadding(inner *clientHello, maxName int) (int, error) {
	data := inner.Ext(extServerName)
	if data == nil {
		return maxName + 9, nil // 没有 SNI：补 M + 9
	}
	r := newRbuf(data)
	list, err := r.vec16() // server_name 的 extension_data 是 ServerNameList
	if err != nil || !r.done() {
		return 0, errShort
	}
	sr := newRbuf(list)
	nameType, err := sr.u8()
	if err != nil {
		return 0, err
	}
	if nameType != 0 {
		return 0, errors.New("ech: name_type 必须是 host_name(0)")
	}
	name, err := sr.vec16()
	if err != nil || !sr.done() {
		return 0, errShort
	}
	if len(name) < maxName {
		return maxName - len(name), nil
	}
	return 0, nil // 名字比 M 还长：补 0，绝不能是负数
}

// roundTo32 —— §6.1.3 第 2 步：N = 31 - ((L - 1) % 32)，返回**要补**的字节数
func roundTo32(length int) int { return 31 - (length-1)%32 }

func encodedClientHelloInner(inner *clientHello, padding int) ([]byte, error) {
	if padding < 0 {
		return nil, errShort
	}
	cleared := inner.copy()
	cleared.SessionID = nil
	body, err := cleared.Encode()
	if err != nil {
		return nil, err
	}
	return append(body, make([]byte, padding)...), nil
}

// compressInner —— 把与 outer 逐字节相同、且名字在 names 里的扩展换成类型列表
func compressInner(inner, outer *clientHello, names []uint16) (*clientHello, error) {
	keep := map[uint16]bool{}
	var order []uint16
	for _, t := range names {
		a, b := inner.extIndex(t), outer.extIndex(t)
		if a < 0 || b < 0 || !bytes.Equal(inner.Exts[a].Data, outer.Exts[b].Data) {
			continue // 内容不同就不能省
		}
		keep[t] = true
		order = append(order, t)
	}
	if len(order) == 0 {
		return inner.copy(), nil
	}
	first := -1
	for i := range inner.Exts {
		if keep[inner.Exts[i].Type] {
			first = i
			break
		}
	}
	marker := make([]byte, 0, 2*len(order))
	for _, t := range order {
		marker = append(marker, byte(t>>8), byte(t))
	}
	dst := inner.copy()
	dst.Exts = nil
	for i := range inner.Exts {
		if keep[inner.Exts[i].Type] {
			if i == first {
				dst.setExt(echOuterExtensions, marker) // 插在被删的第一个扩展的位置
			}
			continue
		}
		dst.setExt(inner.Exts[i].Type, inner.Exts[i].Data)
	}
	return dst, nil
}

func decompressInner(encoded []byte, outer *clientHello) (*clientHello, error) {
	inner, _, err := decodeClientHello(encoded)
	if err != nil {
		return nil, err
	}
	at := inner.extIndex(echOuterExtensions)
	if at < 0 {
		return inner, nil
	}
	raw := inner.Exts[at].Data
	if len(raw) < 2 || len(raw)%2 != 0 {
		return nil, errShort
	}
	var order []uint16
	for i := 0; i < len(raw); i += 2 {
		t := uint16(raw[i])<<8 | uint16(raw[i+1])
		for _, prev := range order {
			if prev == t {
				return nil, errOuterExtDupe
			}
		}
		if t == echExtType {
			return nil, errOuterExtSelf
		}
		if outer.extIndex(t) < 0 {
			return nil, errOuterExtMissing
		}
		order = append(order, t)
	}
	for i := 1; i < len(order); i++ {
		if outer.extIndex(order[i-1]) >= outer.extIndex(order[i]) {
			return nil, errOuterExtOrder // 防放大攻击
		}
	}
	out := inner.copy()
	out.Exts = nil
	for i := range inner.Exts {
		if inner.Exts[i].Type != echOuterExtensions {
			out.setExt(inner.Exts[i].Type, inner.Exts[i].Data)
			continue
		}
		for _, t := range order {
			b := outer.extIndex(t)
			out.setExt(t, outer.Exts[b].Data)
		}
	}
	return out, nil
}
