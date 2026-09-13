// TLV(Type-Length-Value)编解码器,简化版 ASN.1 BER 风格。
// Type=2B BE | Length=短/长格式 | Value=bytes。运行: go run . test | demo
package main

import (
	"bytes"
	"encoding/binary"
	"errors"
	"fmt"
	"log"
	"os"
	"strings"
)

var ErrTLV = errors.New("tlv decode error")

type TLV struct {
	Tag   uint16
	Value []byte
}

func (t TLV) String() string {
	if isPrintable(t.Value) {
		return fmt.Sprintf("TLV{tag=%d, value=%q len=%d}", t.Tag, t.Value, len(t.Value))
	}
	parts := make([]string, len(t.Value))
	for i, b := range t.Value { parts[i] = fmt.Sprintf("%02x", b) }
	return fmt.Sprintf("TLV{tag=%d, value=%s len=%d}", t.Tag, strings.Join(parts, " "), len(t.Value))
}

func isPrintable(b []byte) bool {
	for _, c := range b { if c < 0x20 || c > 0x7e { return false } }
	return true
}

// encodeLength: < 0x80 短格式;>= 0x80 首字节低 7 位为长度字节数 + BE 字节。
func encodeLength(n int) []byte {
	if n < 0x80 { return []byte{byte(n)} }
	bs := make([]byte, 0, 5)
	for n > 0 { bs = append([]byte{byte(n & 0xFF)}, bs...); n >>= 8 }
	return append([]byte{0x80 | byte(len(bs))}, bs...)
}

func decodeLength(buf []byte, pos int) (length, newPos int, err error) {
	if pos >= len(buf) { return 0, 0, fmt.Errorf("%w: out of range", ErrTLV) }
	first := buf[pos]
	if first < 0x80 { return int(first), pos + 1, nil }
	if first == 0x80 { return 0, 0, fmt.Errorf("%w: indefinite length not supported", ErrTLV) }
	nBytes := int(first & 0x7F)
	if nBytes == 0 || pos+1+nBytes > len(buf) {
		return 0, 0, fmt.Errorf("%w: bad long-form byte count %d", ErrTLV, nBytes)
	}
	n := 0
	for i := 1; i <= nBytes; i++ { n = (n << 8) | int(buf[pos+i]) }
	return n, pos + 1 + nBytes, nil
}

func EncodeTLV(tag uint16, value []byte) []byte {
	out := make([]byte, 0, 2+5+len(value))
	out = binary.BigEndian.AppendUint16(out, tag)
	out = append(out, encodeLength(len(value))...)
	return append(out, value...)
}

func DecodeTLV(buf []byte, pos int) (TLV, int, error) {
	if pos+2 > len(buf) { return TLV{}, 0, fmt.Errorf("%w: not enough bytes for tag", ErrTLV) }
	tag := binary.BigEndian.Uint16(buf[pos : pos+2])
	pos += 2
	length, pos, err := decodeLength(buf, pos)
	if err != nil { return TLV{}, 0, err }
	if pos+length > len(buf) {
		return TLV{}, 0, fmt.Errorf("%w: value length %d out of range", ErrTLV, length)
	}
	return TLV{Tag: tag, Value: append([]byte(nil), buf[pos:pos+length]...)}, pos + length, nil
}

func DecodeAll(buf []byte) ([]TLV, error) {
	out := make([]TLV, 0)
	pos := 0
	for pos < len(buf) {
		t, p, err := DecodeTLV(buf, pos)
		if err != nil { return nil, err }
		out = append(out, t)
		pos = p
	}
	return out, nil
}

// ============== 自测 ==============
func hex(b []byte) string {
	parts := make([]string, len(b))
	for i, x := range b { parts[i] = fmt.Sprintf("%02x", x) }
	return strings.Join(parts, " ")
}

func selfTest() {
	fmt.Println("=== TLV codec self-test ===")

	// Case 1: 短格式
	e := EncodeTLV(1, []byte("hello"))
	want := []byte{0x00, 0x01, 0x05, 'h', 'e', 'l', 'l', 'o'}
	if !bytes.Equal(e, want) { log.Fatalf("[1] mismatch got %s want %s", hex(e), hex(want)) }
	t, p, err := DecodeTLV(e, 0)
	check(err)
	if t.Tag != 1 || !bytes.Equal(t.Value, []byte("hello")) || p != len(e) {
		log.Fatalf("[1] parsed mismatch: %+v p=%d", t, p)
	}
	fmt.Printf("  [1] short-form OK  hex=%s  parsed=%s\n", hex(e), t)

	// Case 2: 多条 + 长格式
	ageBuf := make([]byte, 2)
	binary.BigEndian.PutUint16(ageBuf, 30)
	records := []TLV{
		{Tag: 1, Value: []byte("alice")},
		{Tag: 2, Value: ageBuf},
		{Tag: 3, Value: []byte("alice@example.com")},
		{Tag: 4, Value: bytes.Repeat([]byte("x"), 200)},
	}
	var blob []byte
	for _, r := range records { blob = append(blob, EncodeTLV(r.Tag, r.Value)...) }
	fmt.Printf("  [2] four TLVs total=%d bytes  head=%s\n", len(blob), hex(blob[:min(30, len(blob))]))

	decoded, err := DecodeAll(blob)
	check(err)
	if len(decoded) != 4 { log.Fatalf("[3] expected 4 TLVs got %d", len(decoded)) }
	if decoded[0].Tag != 1 || !bytes.Equal(decoded[0].Value, []byte("alice")) {
		log.Fatalf("[3] record 0 mismatch")
	}
	if decoded[1].Tag != 2 || binary.BigEndian.Uint16(decoded[1].Value) != 30 {
		log.Fatalf("[3] record 1 (age) mismatch")
	}
	if !bytes.Equal(decoded[3].Value, bytes.Repeat([]byte("x"), 200)) {
		log.Fatalf("[3] record 3 length mismatch")
	}
	fmt.Printf("  [3] round-trip OK  parsed=%d items\n", len(decoded))

	// Case 4: 长 length(>127 触发 0x82 + 2 字节 BE)
	big := TLV{Tag: 0x1234, Value: bytes.Repeat([]byte("y"), 300)}
	enc := EncodeTLV(big.Tag, big.Value)
	if !(enc[0] == 0x12 && enc[1] == 0x34 && enc[2] == 0x82 && enc[3] == 0x01 && enc[4] == 0x2C) {
		log.Fatalf("[4] long-form header mismatch: %s", hex(enc[:6]))
	}
	fmt.Printf("  [4] long-form length OK  first6=%s  total=%dB\n", hex(enc[:6]), len(enc))

	// Case 5: 空 value
	e2 := EncodeTLV(7, nil)
	if !bytes.Equal(e2, []byte{0x00, 0x07, 0x00}) { log.Fatalf("[5] mismatch %s", hex(e2)) }
	t, _, _ = DecodeTLV(e2, 0)
	if len(t.Value) != 0 { log.Fatalf("[5] empty value decoded non-empty") }
	fmt.Printf("  [5] empty value OK  hex=%s\n", hex(e2))

	// Case 6: 截断检测
	if _, _, err := DecodeTLV([]byte{0x00, 0x01, 0xFF}, 0); err == nil {
		log.Fatal("[6] expected truncated-length error")
	} else {
		fmt.Printf("  [6] truncated length detected: %v\n", err)
	}

	fmt.Println("all self-tests passed.")
}

func check(err error) { if err != nil { log.Fatal(err) } }
func min(a, b int) int { if a < b { return a }; return b }

// ============== Person demo ==============
type Person struct {
	Name  string
	Age   uint16
	Email string
}

const (tagName uint16 = 1; tagAge uint16 = 2; tagEmail uint16 = 3)

func (p Person) Encode() []byte {
	out := EncodeTLV(tagName, []byte(p.Name))
	ageBuf := make([]byte, 2)
	binary.BigEndian.PutUint16(ageBuf, p.Age)
	out = append(out, EncodeTLV(tagAge, ageBuf)...)
	return append(out, EncodeTLV(tagEmail, []byte(p.Email))...)
}

func DecodePerson(buf []byte) (Person, error) {
	items, err := DecodeAll(buf)
	if err != nil { return Person{}, err }
	p := Person{}
	for _, t := range items {
		switch t.Tag {
		case tagName:  p.Name = string(t.Value)
		case tagAge:   p.Age = binary.BigEndian.Uint16(t.Value)
		case tagEmail: p.Email = string(t.Value)
		default:       fmt.Printf("  unknown tag=%d skipped\n", t.Tag)
		}
	}
	return p, nil
}

func demoPerson() {
	fmt.Println("=== Person serialization demo ===")
	p1 := Person{Name: "张三", Age: 28, Email: "zhang@example.com"}
	blob := p1.Encode()
	fmt.Printf("  encoded %d bytes: %s\n", len(blob), hex(blob))
	p2, err := DecodePerson(blob)
	check(err)
	if p1 != p2 { log.Fatalf("round-trip mismatch: %+v vs %+v", p1, p2) }
	fmt.Printf("  decoded: name=%q age=%d email=%q\n", p2.Name, p2.Age, p2.Email)
}

func main() {
	if len(os.Args) >= 2 {
		switch os.Args[1] {
		case "test": selfTest(); demoPerson(); return
		case "demo": demoPerson(); return
		}
	}
	fmt.Fprintln(os.Stderr, "usage: tlv_codec test | demo")
	os.Exit(1)
}