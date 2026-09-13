// protobuf_wire: Go implementation of the Protobuf 3 wire format.
//
// Implements six wire types (DEPRECATED start/end group included for completeness):
//   VARINT(0)  -> int32, int64, uint32, uint64, bool, enum
//   FIXED64(1) -> fixed64, sfixed64, double
//   LEN(2)     -> string, bytes, embedded messages, packed repeated
//   SGROUP(3)  -> deprecated
//   EGROUP(4)  -> deprecated
//   FIXED32(5) -> fixed32, sfixed32, float
//
// Tag formula: (fieldNumber << 3) | wireType
//
//   go run protobuf_wire.go
package main

import (
	"bytes"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
)

// --------- varint ---------
func writeVarint(w *bytes.Buffer, v uint64) {
	for v >= 0x80 {
		w.WriteByte(byte(v) | 0x80)
		v >>= 7
	}
	w.WriteByte(byte(v))
}

func readVarint(r io.ByteReader) (uint64, error) {
	var result uint64
	var shift uint
	for i := 0; i < 10; i++ {
		b, err := r.ReadByte()
		if err != nil {
			return 0, err
		}
		result |= uint64(b&0x7f) << shift
		if b&0x80 == 0 {
			return result, nil
		}
		shift += 7
	}
	return 0, errors.New("varint > 10 bytes")
}

func zigzag32(n int32) uint32 { return uint32(n<<1) ^ uint32(n>>31) }
func zigzag64(n int64) uint64 { return uint64(n<<1) ^ uint64(n>>63) }

func unzigzag32(v uint32) int32 { return int32(v>>1) ^ -int32(v&1) }
func unzigzag64(v uint64) int64 { return int64(v>>1) ^ -int64(v&1) }

// --------- Field / Message ---------
type Field struct {
	Number uint32
	Wire   uint8
	Value  interface{}
}

// Message is an ordered list of fields (matches the on-wire order).
type Message struct {
	Fields []Field
}

func (m *Message) AddInt(number uint32, v int64) {
	m.Fields = append(m.Fields, Field{Number: number, Wire: 0, Value: v})
}
func (m *Message) AddString(number uint32, s string) {
	m.Fields = append(m.Fields, Field{Number: number, Wire: 2, Value: s})
}
func (m *Message) AddBytes(number uint32, b []byte) {
	m.Fields = append(m.Fields, Field{Number: number, Wire: 2, Value: b})
}
func (m *Message) AddSub(number uint32, sub *Message) {
	m.Fields = append(m.Fields, Field{Number: number, Wire: 2, Value: sub})
}
func (m *Message) AddPackedInts(number uint32, xs []int64) {
	var body bytes.Buffer
	for _, v := range xs {
		writeVarint(&body, uint64(v))
	}
	packed := body.Bytes()
	m.Fields = append(m.Fields, Field{Number: number, Wire: 2, Value: packed})
}

// --------- encode ---------
func makeTag(fn uint32, wt uint8) uint32 { return (fn << 3) | uint32(wt) }

func (m *Message) Encode() []byte {
	var buf bytes.Buffer
	for _, f := range m.Fields {
		writeVarint(&buf, makeTag(f.Number, f.Wire))
		switch f.Wire {
		case 0: // VARINT
			v := f.Value.(int64)
			writeVarint(&buf, uint64(v))
		case 1: // FIXED64
			binary.Write(&buf, binary.LittleEndian, f.Value.(uint64))
		case 5: // FIXED32
			binary.Write(&buf, binary.LittleEndian, f.Value.(uint32))
		case 2: // LEN
			if sub, ok := f.Value.(*Message); ok {
				body := sub.Encode()
				writeVarint(&buf, uint64(len(body)))
				buf.Write(body)
			} else if packed, ok := f.Value.([]byte); ok {
				// packed repeated primitives
				writeVarint(&buf, uint64(len(packed)))
				buf.Write(packed)
			} else if s, ok := f.Value.(string); ok {
				b := []byte(s)
				writeVarint(&buf, uint64(len(b)))
				buf.Write(b)
			} else if b, ok := f.Value.([]byte); ok {
				writeVarint(&buf, uint64(len(b)))
				buf.Write(b)
			}
		}
	}
	return buf.Bytes()
}

// --------- decode ---------
type byteReader struct{ *bytes.Reader }

// decodeMessage reads a single message frame starting at the current pos.
func decodeMessage(r io.ByteReader) (*Message, error) {
	msg := &Message{}
	for {
		tag, err := readVarint(r)
		if err == io.EOF {
			return msg, nil
		}
		if err != nil {
			return nil, err
		}
		fn := tag >> 3
		wt := uint8(tag & 0x7)
		switch wt {
		case 0:
			v, err := readVarint(r)
			if err != nil {
				return nil, err
			}
			msg.Fields = append(msg.Fields, Field{Number: fn, Wire: wt, Value: int64(v)})
		case 1:
			var v uint64
			if err := binary.Read(struct{ io.ByteReader }{r}, binary.LittleEndian, &v); err != nil {
				return nil, err
			}
			msg.Fields = append(msg.Fields, Field{Number: fn, Wire: wt, Value: v})
		case 5:
			var v uint32
			if err := binary.Read(struct{ io.ByteReader }{r}, binary.LittleEndian, &v); err != nil {
				return nil, err
			}
			msg.Fields = append(msg.Fields, Field{Number: fn, Wire: wt, Value: v})
		case 2:
			ln, err := readVarint(r)
			if err != nil {
				return nil, err
			}
			body := make([]byte, ln)
			if _, err := io.ReadFull(struct{ io.Reader }{r}, body); err != nil {
				return nil, err
			}
			// Try to interpret as sub-message: peek first varint and check
			// if its low 3 bits form a valid wire type and field range.
			if sub, ok := trySubMessage(body); ok {
				msg.Fields = append(msg.Fields, Field{Number: fn, Wire: wt, Value: sub})
			} else {
				msg.Fields = append(msg.Fields, Field{Number: fn, Wire: wt, Value: body})
			}
		case 3, 4: // deprecated groups
		default:
			return nil, fmt.Errorf("unknown wire type %d", wt)
		}
	}
}

// trySubMessage best-effort recursive decode: returns true if decode succeeds
// AND consumes the entire body; otherwise it's bytes.
func trySubMessage(body []byte) (*Message, bool) {
	if len(body) == 0 {
		return nil, false
	}
	r := &byteReader{Reader: bytes.NewReader(body)}
	sub, err := decodeMessage(r)
	if err != nil || r.Len() != 0 {
		return nil, false
	}
	return sub, true
}

// --------- self-test ---------
func main() {
	want := []byte{0x08, 0x96, 0x01}
	m := &Message{}
	m.AddInt(1, 150)
	if got := m.Encode(); !bytes.Equal(got, want) {
		panic(fmt.Sprintf("Encode() = %x, want %x", got, want))
	}
	r := &byteReader{Reader: bytes.NewReader(want)}
	dec, err := decodeMessage(r)
	if err != nil {
		panic(err)
	}
	if len(dec.Fields) != 1 || dec.Fields[0].Number != 1 ||
		dec.Fields[0].Wire != 0 || dec.Fields[0].Value.(int64) != 150 {
		panic("decode mismatch")
	}

	// Test 2: string field 2 = "testing"
	m2 := &Message{}
	m2.AddString(2, "testing")
	want2 := []byte{0x12, 0x07, 't', 'e', 's', 't', 'i', 'n', 'g'}
	if got := m2.Encode(); !bytes.Equal(got, want2) {
		panic(fmt.Sprintf("Encode() = %x, want %x", got, want2))
	}

	// Test 3: nested message
	inner := &Message{}
	inner.AddInt(1, 150)
	outer := &Message{}
	outer.AddSub(3, inner)
	want3 := []byte{0x1a, 0x03, 0x08, 0x96, 0x01}
	if got := outer.Encode(); !bytes.Equal(got, want3) {
		panic(fmt.Sprintf("Encode() = %x, want %x", got, want3))
	}

	// Test 4: zigzag
	if uint32(zigzag32(-1)) != 1 {
		panic("zigzag -1 != 1")
	}
	if unzigzag32(1) != -1 {
		panic("unzigzag 1 != -1")
	}
	if unzigzag32(3) != -2 {
		panic("unzigzag 3 != -2")
	}

	fmt.Println("ALL self-tests PASS")
}
