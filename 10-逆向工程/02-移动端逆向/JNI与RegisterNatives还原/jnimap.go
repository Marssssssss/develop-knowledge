// jnimap.go — 与 jnimap.py 同语义的 Go 复刻(静态审查用)。
package main

import (
	"encoding/binary"
	"fmt"
	"strings"
)

const registerNativesIndex = 215 // JNI 函数表第 215 项

func mangleIdent(s string) string {
	var b strings.Builder
	for _, ch := range s {
		switch {
		case ch == '_':
			b.WriteString("_1")
		case ch > 0x7f:
			fmt.Fprintf(&b, "_0%04x", ch)
		default:
			b.WriteRune(ch)
		}
	}
	return b.String()
}

func mangleArgs(descriptor string) string {
	args := descriptor[1:strings.LastIndex(descriptor, ")")]
	var b strings.Builder
	for _, ch := range args {
		switch ch {
		case '_':
			b.WriteString("_1")
		case ';':
			b.WriteString("_2")
		case '[':
			b.WriteString("_3")
		case '/':
			b.WriteByte('_')
		default:
			if ch > 0x7f {
				fmt.Fprintf(&b, "_0%04x", ch)
			} else {
				b.WriteRune(ch)
			}
		}
	}
	return b.String()
}

func shortName(fqcn, method string) string {
	parts := strings.Split(fqcn, "/")
	for i, p := range parts {
		parts[i] = mangleIdent(p)
	}
	return "Java_" + strings.Join(parts, "_") + "_" + mangleIdent(method)
}

func longName(fqcn, method, descriptor string) string {
	return shortName(fqcn, method) + "__" + mangleArgs(descriptor)
}

// resolve 镜像 VM 解析序:先短名,有 native 重载才查长名。
func resolve(exported map[string]uint64, fqcn, method, descriptor string, nativeOverload bool) (string, string) {
	if _, ok := exported[shortName(fqcn, method)]; ok {
		return "short", shortName(fqcn, method)
	}
	if nativeOverload {
		ln := longName(fqcn, method, descriptor)
		if _, ok := exported[ln]; ok {
			return "long", ln
		}
	}
	return "", ""
}

// readTable 解析 JNINativeMethod{name*, sig*, fnPtr} 数组。
func readTable(buf []byte, off, n int) [][3]uint64 {
	out := make([][3]uint64, 0, n)
	for i := 0; i < n; i++ {
		e := off + i*24
		out = append(out, [3]uint64{
			binary.LittleEndian.Uint64(buf[e:]),
			binary.LittleEndian.Uint64(buf[e+8:]),
			binary.LittleEndian.Uint64(buf[e+16:]),
		})
	}
	return out
}

func main() {
	fmt.Println(shortName("java/lang/String", "indexOf"))                 // Java_java_lang_String_indexOf
	fmt.Println(mangleArgs("(Ljava/lang/String;[I)V"))                    // Ljava_lang_String_2_3I
	fmt.Println(longName("Cls2", "f", "(D)D"))                            // Java_Cls2_f__D
	k, s := resolve(map[string]uint64{"Java_Cls2_f__D": 0x2040}, "Cls2", "f", "(D)D", true)
	fmt.Println(k, s)                                                     // long Java_Cls2_f__D
	fmt.Println("RegisterNatives idx:", registerNativesIndex)
}
