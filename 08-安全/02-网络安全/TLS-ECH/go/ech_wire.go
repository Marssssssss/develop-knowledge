// ech_wire.go —— ClientHello / ECHConfig / ECHClientHello 的编解码（RFC 9849 §4-§5）
//
// ClientHello（**不含** 4 字节握手头）必须逐字节忠实：ECH 的 AAD 就是这个结构的
// 序列化结果（§5.2），只做个「像 TLS」的近似会让 AAD 对不上，而且不会有任何报错。
//
// 写入侧用「先占位、后回填」的长度前缀（vec16Start/vec16End），省掉序列化两遍；
// 读取侧任何越界都返回错误 —— 这一层解析的是对端发来的字节与 DNS 里拉来的配置，
// 越界读就是可得的内存安全问题。
package main

import "errors"

const (
	echVersion         = 0xFE0D
	echExtType         = 0xFE0D // encrypted_client_hello
	echOuterExtensions = 0xFD00 // ech_outer_extensions

	extServerName       = 0x0000
	extSupportedVersion = 0x002B
	extKeyShare         = 0x0033
)

var errShort = errors.New("ech: 缓冲区越界或长度前缀非法")

// ---------------------------------------------------------------- 写

type wbuf struct{ b []byte }

func (w *wbuf) put(data []byte) { w.b = append(w.b, data...) }
func (w *wbuf) u8(v byte)       { w.b = append(w.b, v) }
func (w *wbuf) u16(v uint16)    { w.b = append(w.b, byte(v>>8), byte(v)) }

func (w *wbuf) vec16Start() int {
	at := len(w.b)
	w.b = append(w.b, 0, 0)
	return at
}

func (w *wbuf) vec16End(at int) error {
	n := len(w.b) - at - 2
	if n < 0 || n > 0xffff {
		return errShort
	}
	w.b[at] = byte(n >> 8)
	w.b[at+1] = byte(n)
	return nil
}

// ---------------------------------------------------------------- 读

type rbuf struct {
	b   []byte
	pos int
}

func newRbuf(b []byte) *rbuf { return &rbuf{b: b} }

func (r *rbuf) take(n int) ([]byte, error) {
	if n < 0 || r.pos+n > len(r.b) {
		return nil, errShort
	}
	v := r.b[r.pos : r.pos+n]
	r.pos += n
	return v, nil
}

func (r *rbuf) u8() (byte, error) {
	v, err := r.take(1)
	if err != nil {
		return 0, err
	}
	return v[0], nil
}

func (r *rbuf) u16() (uint16, error) {
	v, err := r.take(2)
	if err != nil {
		return 0, err
	}
	return uint16(v[0])<<8 | uint16(v[1]), nil
}

func (r *rbuf) vec8() ([]byte, error) {
	n, err := r.u8()
	if err != nil {
		return nil, err
	}
	return r.take(int(n))
}

func (r *rbuf) vec16() ([]byte, error) {
	n, err := r.u16()
	if err != nil {
		return nil, err
	}
	return r.take(int(n))
}

func (r *rbuf) done() bool { return r.pos == len(r.b) }

func clone(b []byte) []byte { return append([]byte(nil), b...) }

// ---------------------------------------------------------------- ClientHello

type ext struct {
	Type uint16
	Data []byte
}

type clientHello struct {
	Random       []byte
	SessionID    []byte
	CipherSuites []uint16
	Compression  []byte
	Exts         []ext
}

func (ch *clientHello) copy() *clientHello {
	c := *ch
	c.Random = clone(ch.Random)
	c.SessionID = clone(ch.SessionID)
	c.Compression = clone(ch.Compression)
	c.CipherSuites = append([]uint16(nil), ch.CipherSuites...)
	c.Exts = make([]ext, len(ch.Exts))
	for i := range ch.Exts {
		c.Exts[i] = ext{Type: ch.Exts[i].Type, Data: clone(ch.Exts[i].Data)}
	}
	return &c
}

func (ch *clientHello) extIndex(typ uint16) int {
	for i := range ch.Exts {
		if ch.Exts[i].Type == typ {
			return i
		}
	}
	return -1
}

func (ch *clientHello) Ext(typ uint16) []byte {
	if i := ch.extIndex(typ); i >= 0 {
		return ch.Exts[i].Data
	}
	return nil
}

// setExt —— 已存在则原地覆盖，否则追加到末尾（ECH 扩展必须最后追加，见 §6.1）
func (ch *clientHello) setExt(typ uint16, data []byte) {
	if i := ch.extIndex(typ); i >= 0 {
		ch.Exts[i].Data = clone(data)
		return
	}
	ch.Exts = append(ch.Exts, ext{Type: typ, Data: clone(data)})
}

func (ch *clientHello) Encode() ([]byte, error) {
	w := &wbuf{}
	w.u16(0x0303) // legacy_version
	w.put(ch.Random)
	w.u8(byte(len(ch.SessionID)))
	w.put(ch.SessionID)
	at := w.vec16Start()
	for _, cs := range ch.CipherSuites {
		w.u16(cs)
	}
	if err := w.vec16End(at); err != nil {
		return nil, err
	}
	w.u8(byte(len(ch.Compression)))
	w.put(ch.Compression)
	at = w.vec16Start()
	for _, e := range ch.Exts {
		w.u16(e.Type)
		w.u16(uint16(len(e.Data)))
		w.put(e.Data)
	}
	if err := w.vec16End(at); err != nil {
		return nil, err
	}
	return w.b, nil
}

// decodeClientHello —— consumed 返回 ClientHello 自身的长度：
// EncodedClientHelloInner 后面还跟着零填充，调用方要自己判那一段（§5.1）
func decodeClientHello(data []byte) (ch *clientHello, consumed int, err error) {
	r := newRbuf(data)
	ver, err := r.u16()
	if err != nil {
		return nil, 0, err
	}
	if ver != 0x0303 {
		return nil, 0, errors.New("ech: legacy_version 必须是 0x0303")
	}
	ch = &clientHello{}
	if ch.Random, err = r.take(32); err != nil {
		return nil, 0, err
	}
	ch.Random = clone(ch.Random)
	if ch.SessionID, err = r.vec8(); err != nil {
		return nil, 0, err
	}
	ch.SessionID = clone(ch.SessionID)
	blob, err := r.vec16()
	if err != nil {
		return nil, 0, err
	}
	if len(blob) == 0 || len(blob)%2 != 0 {
		return nil, 0, errors.New("ech: cipher_suites 长度非法")
	}
	for i := 0; i < len(blob); i += 2 {
		ch.CipherSuites = append(ch.CipherSuites, uint16(blob[i])<<8|uint16(blob[i+1]))
	}
	if ch.Compression, err = r.vec8(); err != nil {
		return nil, 0, err
	}
	ch.Compression = clone(ch.Compression)
	if len(ch.Compression) == 0 {
		return nil, 0, errors.New("ech: compression_methods 不能为空")
	}
	blob, err = r.vec16()
	if err != nil {
		return nil, 0, err
	}
	er := newRbuf(blob)
	for !er.done() {
		typ, err := er.u16()
		if err != nil {
			return nil, 0, err
		}
		data, err := er.vec16()
		if err != nil {
			return nil, 0, err
		}
		if ch.extIndex(typ) >= 0 {
			return nil, 0, errors.New("ech: 扩展类型重复（RFC 8446 §4.2 禁止）")
		}
		ch.Exts = append(ch.Exts, ext{Type: typ, Data: clone(data)})
	}
	return ch, r.pos, nil
}
