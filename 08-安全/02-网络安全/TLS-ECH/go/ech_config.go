// ech_config.go —— ECHConfig 与 ECHClientHello 的编解码（RFC 9849 §4.1、§5）
//
// ECHConfig 是服务端通过 HTTPS DNS 记录（或 DNS 里的 ech 参数）发布会话密钥的部分。
// 注意 **KEM 公钥与套件列表都带长度前缀**，而 maximum_name_length / public_name 是
// u8 前缀 —— 少一层的写法在本地自测里完全正常，只有和真实配置对拍才会暴露。
//
// ECHClientHello（ECH 扩展的内容）只有 outer 一种变体需要序列化：inner 变体是 Empty，
// 所以解码函数对非 0 的首字节一律拒绝。
package main

import "errors"

// ---------------------------------------------------------------- ECHConfig（§4.1）

type hpkeKeyConfig struct {
	ConfigID     byte
	KemID        uint16
	PublicKey    []byte
	CipherSuites [][2]uint16
}

type echConfig struct {
	KeyConfig     hpkeKeyConfig
	MaxNameLength byte
	PublicName    []byte
	Version       uint16
}

func (c *echConfig) Encode() ([]byte, error) {
	w := &wbuf{}
	w.u16(echVersion)
	at := w.vec16Start()
	w.u8(c.KeyConfig.ConfigID)
	w.u16(c.KeyConfig.KemID)
	w.u16(uint16(len(c.KeyConfig.PublicKey)))
	w.put(c.KeyConfig.PublicKey)
	inner := w.vec16Start()
	for _, s := range c.KeyConfig.CipherSuites {
		w.u16(s[0])
		w.u16(s[1])
	}
	if err := w.vec16End(inner); err != nil {
		return nil, err
	}
	w.u8(c.MaxNameLength)
	w.u8(byte(len(c.PublicName)))
	w.put(c.PublicName)
	inner = w.vec16Start() // extensions：本 demo 恒空
	if err := w.vec16End(inner); err != nil {
		return nil, err
	}
	if err := w.vec16End(at); err != nil {
		return nil, err
	}
	return w.b, nil
}

// decodeEchConfig —— 只接受本 demo 支持的版本与「无扩展」的 contents；
// 真实实现里不认识的版本要靠 length 跳过（§4.2），见 README 的跨语言一致性一节
func decodeEchConfig(data []byte) (*echConfig, error) {
	r := newRbuf(data)
	ver, err := r.u16()
	if err != nil {
		return nil, err
	}
	if ver != echVersion {
		return nil, errors.New("ech: 不认识的 ECHConfig 版本")
	}
	body, err := r.vec16()
	if err != nil || !r.done() {
		return nil, errShort
	}
	b := newRbuf(body)
	c := &echConfig{Version: ver}
	if c.KeyConfig.ConfigID, err = b.u8(); err != nil {
		return nil, err
	}
	if c.KeyConfig.KemID, err = b.u16(); err != nil {
		return nil, err
	}
	if c.KeyConfig.PublicKey, err = b.vec16(); err != nil || len(c.KeyConfig.PublicKey) == 0 {
		return nil, errShort
	}
	suites, err := b.vec16()
	if err != nil || len(suites) == 0 || len(suites)%4 != 0 {
		return nil, errShort
	}
	for i := 0; i < len(suites); i += 4 {
		c.KeyConfig.CipherSuites = append(c.KeyConfig.CipherSuites,
			[2]uint16{uint16(suites[i])<<8 | uint16(suites[i+1]),
				uint16(suites[i+2])<<8 | uint16(suites[i+3])})
	}
	if c.MaxNameLength, err = b.u8(); err != nil {
		return nil, err
	}
	if c.PublicName, err = b.vec8(); err != nil || len(c.PublicName) == 0 {
		return nil, errShort
	}
	exts, err := b.vec16()
	if err != nil || !b.done() {
		return nil, errShort
	}
	if len(exts) != 0 {
		return nil, errors.New("ech: 本实现不支持带扩展的 ECHConfig")
	}
	return c, nil
}

// ---------------------------------------------------------------- ECHClientHello（§5）

type outerExt struct {
	KdfID    uint16
	AeadID   uint16
	ConfigID byte
	Enc      []byte
	Payload  []byte
}

func encodeOuterExt(o *outerExt) ([]byte, error) {
	w := &wbuf{}
	w.u8(0x00) // ECHClientHelloType = outer(0)
	w.u16(o.KdfID)
	w.u16(o.AeadID)
	w.u8(o.ConfigID)
	w.u16(uint16(len(o.Enc)))
	w.put(o.Enc)
	w.u16(uint16(len(o.Payload)))
	w.put(o.Payload)
	return w.b, nil
}

func decodeOuterExt(data []byte) (*outerExt, error) {
	r := newRbuf(data)
	typ, err := r.u8()
	if err != nil {
		return nil, err
	}
	if typ != 0x00 {
		return nil, errors.New("ech: 不是 outer 变体")
	}
	o := &outerExt{}
	if o.KdfID, err = r.u16(); err != nil {
		return nil, err
	}
	if o.AeadID, err = r.u16(); err != nil {
		return nil, err
	}
	if o.ConfigID, err = r.u8(); err != nil {
		return nil, err
	}
	if o.Enc, err = r.vec16(); err != nil {
		return nil, err
	}
	if o.Payload, err = r.vec16(); err != nil || !r.done() {
		return nil, errShort
	}
	return o, nil
}
