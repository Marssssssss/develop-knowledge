package main

import "fmt"

func constraint(field string, v int) *Pattern {
	return &Pattern{Kind: "constraint", Field: field, Value: v}
}

func operand(name string) *Pattern {
	return &Pattern{Kind: "operand", Field: name}
}

func and(a, b *Pattern) *Pattern { return &Pattern{Kind: "and", Left: a, Right: b} }

func main() {
	fmt.Println("== 1. token 的位编号：最低位是 0 ==")
	t := NewToken("word", 16, "")
	if err := t.AddField("lo8", 0, 7); err != nil {
		panic(err)
	}
	if err := t.AddField("hi8", 8, 15); err != nil {
		panic(err)
	}
	for _, e := range []string{"big", "little"} {
		v := t.Value([]byte{0x12, 0x34}, e)
		fmt.Printf("  %-6s -> 0x%04x  hi8=0x%02x lo8=0x%02x\n",
			e, v, t.FieldValue("hi8", v), t.FieldValue("lo8", v))
	}

	fmt.Println("== 2. signed 属性 ==")
	d := NewToken("data8", 8, "")
	if err := d.AddField("rel", 0, 7, "signed"); err != nil {
		panic(err)
	}
	fmt.Printf("  0xFE 作为 signed = %d，显示 %s\n",
		d.FieldValue("rel", 0xFE), d.Display("rel", 0xFE))

	fmt.Println("== 3. attach variables ==")
	sp := NewSpec()
	if err := sp.DefineEndian("little"); err != nil {
		panic(err)
	}
	tok := NewToken("opbyte", 8, "")
	if err := tok.AddField("op", 0, 7); err != nil {
		panic(err)
	}
	if err := tok.AddField("op6", 2, 7); err != nil {
		panic(err)
	}
	if err := tok.AddField("r1", 0, 1); err != nil {
		panic(err)
	}
	if err := sp.DefineToken(tok); err != nil {
		panic(err)
	}
	if err := sp.AttachVariables([]string{"r1"}, []string{"R0", "R1", "R2", "R3"}); err != nil {
		panic(err)
	}
	fmt.Printf("  r1 -> %v\n", sp.Attach["r1"])

	sp.Constructors = append(sp.Constructors,
		&Constructor{Table: "", Mnemonic: "halt", Pattern: constraint("op", 0x00)},
		&Constructor{Table: "", Mnemonic: "nop", Pattern: constraint("op", 0xEA)},
		&Constructor{Table: "", Mnemonic: "inc", Operands: []string{"r1"},
			Pattern: and(constraint("op6", 0x3E), operand("r1"))},
		&Constructor{Table: "", Mnemonic: "lda", Operands: []string{"r1"},
			Pattern: and(constraint("op6", 0x3D), operand("r1"))},
	)

	fmt.Println("== 4. 解码 ==")
	for _, b := range []byte{0x00, 0xEA, 0x3E<<2 | 2, 0x3D<<2 | 3, 0xFF} {
		ins := sp.Decode([]byte{b})
		if ins == nil {
			fmt.Printf("  %#04x -> 无匹配\n", b)
			continue
		}
		fmt.Printf("  %#04x -> %s %v\n", b, ins.Mnemonic, ins.Operands)
	}

	fmt.Println("== 5. `...` 处理变长指令 ==")
	v := NewSpec()
	if err := v.DefineEndian("little"); err != nil {
		panic(err)
	}
	vt := NewToken("opbyte", 8, "")
	if err := vt.AddField("op", 0, 7); err != nil {
		panic(err)
	}
	if err := v.DefineToken(vt); err != nil {
		panic(err)
	}
	dt := NewToken("data8", 8, "")
	if err := dt.AddField("imm8", 0, 7); err != nil {
		panic(err)
	}
	if err := v.DefineToken(dt); err != nil {
		panic(err)
	}
	v.Constructors = append(v.Constructors,
		&Constructor{Table: "", Mnemonic: "lda", Operands: []string{"imm8"},
			Pattern: and(and(constraint("op", 0xA9), &Pattern{Kind: "ellipsis"}), operand("imm8"))},
	)
	ins := v.Decode([]byte{0xA9, 0x99})
	if ins == nil {
		fmt.Println("  A9 99 -> 无匹配")
	} else {
		fmt.Printf("  A9 99 -> %s 长度 %d 操作数 %v\n", ins.Mnemonic, ins.Length, ins.Operands)
	}
	fmt.Printf("  A9    -> %v（字节不够）\n", v.Decode([]byte{0xA9}) == nil)
}
