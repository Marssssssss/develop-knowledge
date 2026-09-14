// ClientHello 解析与构造（供 tls_fingerprint.go 回环测试使用）。
package main

import "encoding/binary"

// ---------- ClientHello ----------
type clientHello struct {
	legacyVersion     uint16
	ciphers           []uint16
	exts              []uint16
	sni               bool
	alpnFirst         string
	supportedVersions []uint16
	sigalgs           []uint16
	curves            []uint16
	pointFormats      []uint8
}

type be struct{ b []byte }

func (r *be) u8() uint8      { v := r.b[0]; r.b = r.b[1:]; return v }
func (r *be) u16() uint16    { v := binary.BigEndian.Uint16(r.b); r.b = r.b[2:]; return v }
func (r *be) take(n int) []byte { v := r.b[:n]; r.b = r.b[n:]; return v }

func parseClientHello(data []byte) *clientHello {
	ch := &clientHello{}
	r := &be{data}
	if r.u8() != 0x16 {
		panic("not handshake record")
	}
	r.u16()          // record version
	recLen := int(r.u16())
	body := r.take(recLen)
	if r.u8() != 0x01 {
		panic("not ClientHello")
	}
	hlen := int(body[1])<<16 | int(body[2])<<8 | int(body[3])
	hello := body[4 : 4+hlen]
	h := &be{hello}
	ch.legacyVersion = h.u16()
	h.take(32) // random
	h.take(int(h.u8())) // session id
	csLen := int(h.u16())
	for i := 0; i < csLen; i += 2 {
		ch.ciphers = append(ch.ciphers, h.u16())
	}
	h.take(int(h.u8())) // compression
	extsLen := int(h.u16())
	end := len(h.b) - extsLen
	for len(h.b) > end {
		et := h.u16()
		ed := h.take(int(h.u16()))
		ch.exts = append(ch.exts, et)
		switch et {
		case 0x0000:
			ch.sni = true
		case 0x0010: // ALPN: list_len(2) + proto_len(1) + proto
			if len(ed) >= 4 {
				ch.alpnFirst = string(ed[3 : 3+int(ed[2])])
			}
		case 0x002b: // supported_versions
			n := int(ed[0])
			for i := 1; i+1 < 1+n; i += 2 {
				ch.supportedVersions = append(ch.supportedVersions,
					binary.BigEndian.Uint16(ed[i:]))
			}
		case 0x000d: // signature_algorithms
			n := int(binary.BigEndian.Uint16(ed))
			for i := 2; i+1 < 2+n; i += 2 {
				ch.sigalgs = append(ch.sigalgs, binary.BigEndian.Uint16(ed[i:]))
			}
		case 0x000a: // supported_groups
			n := int(binary.BigEndian.Uint16(ed))
			for i := 2; i+1 < 2+n; i += 2 {
				ch.curves = append(ch.curves, binary.BigEndian.Uint16(ed[i:]))
			}
		case 0x000b: // ec_point_formats
			n := int(ed[0])
			ch.pointFormats = append(ch.pointFormats, ed[1:1+n]...)
		}
	}
	return ch
}

// ---------- 构造 (回环测试用) ----------
func ext(t uint16, data []byte) []byte {
	out := make([]byte, 4+len(data))
	binary.BigEndian.PutUint16(out, t)
	binary.BigEndian.PutUint16(out[2:], uint16(len(data)))
	copy(out[4:], data)
	return out
}

type helloArgs struct {
	version  uint16
	ciphers  []uint16
	exts     [][]byte // 已编码扩展
	sigalgs  []uint16
	curves   []uint16
	pf       []uint8
	sni      []byte
	alpn     []string
	sv       []uint16
}

func u16s(xs []uint16) []byte {
	out := make([]byte, 2*len(xs))
	for i, x := range xs {
		binary.BigEndian.PutUint16(out[2*i:], x)
	}
	return out
}

func buildClientHello(a helloArgs) []byte {
	var blob []byte
	v := make([]byte, 2)
	binary.BigEndian.PutUint16(v, a.version)
	blob = append(blob, v...)
	blob = append(blob, make([]byte, 32)...) // random
	blob = append(blob, 0)                   // session id 空
	cl := make([]byte, 2)
	binary.BigEndian.PutUint16(cl, uint16(2*len(a.ciphers)))
	blob = append(blob, cl...)
	blob = append(blob, u16s(a.ciphers)...)
	blob = append(blob, 1, 0) // compression: null
	parts := append([][]byte{}, a.exts...)
	if a.sni != nil {
		d := make([]byte, 5+len(a.sni))
		binary.BigEndian.PutUint16(d, uint16(3+len(a.sni)))
		d[2] = 0 // name_type
		binary.BigEndian.PutUint16(d[3:], uint16(len(a.sni)))
		copy(d[5:], a.sni)
		parts = append(parts, ext(0x0000, d))
	}
	if len(a.curves) > 0 {
		d := append([]byte{0, 0}, u16s(a.curves)...)
		binary.BigEndian.PutUint16(d, uint16(2*len(a.curves)))
		parts = append(parts, ext(0x000a, d))
	}
	if len(a.pf) > 0 {
		parts = append(parts, ext(0x000b, append([]byte{byte(len(a.pf))}, a.pf...)))
	}
	if len(a.sigalgs) > 0 {
		d := append([]byte{0, 0}, u16s(a.sigalgs)...)
		binary.BigEndian.PutUint16(d, uint16(2*len(a.sigalgs)))
		parts = append(parts, ext(0x000d, d))
	}
	if len(a.alpn) > 0 {
		var al []byte
		for _, p := range a.alpn {
			al = append(al, byte(len(p)))
			al = append(al, p...)
		}
		d := make([]byte, 2+len(al))
		binary.BigEndian.PutUint16(d, uint16(len(al)))
		copy(d[2:], al)
		parts = append(parts, ext(0x0010, d))
	}
	if len(a.sv) > 0 {
		d := append([]byte{byte(2 * len(a.sv))}, u16s(a.sv)...)
		parts = append(parts, ext(0x002b, d))
	}
	total := 0
	for _, p := range parts {
		total += len(p)
	}
	el := make([]byte, 2)
	binary.BigEndian.PutUint16(el, uint16(total))
	blob = append(blob, el...)
	for _, p := range parts {
		blob = append(blob, p...)
	}
	hs := append([]byte{0x01, byte(len(blob) >> 16), byte(len(blob) >> 8), byte(len(blob))}, blob...)
	rec := []byte{0x16, 0x03, 0x01, byte(len(hs) >> 8), byte(len(hs))}
	return append(rec, hs...)
}

