// ech_main.go —— TLS 1.3 ECH（Encrypted Client Hello, RFC 9849）自检主程序
//
// 运行：go run ./go        （或 go build ./go && ./go）
// 成功时退出码 0，失败打印每条 FAIL 的标签与补充信息。
//
// 七个用例组：
//
//	A  RFC 9180 附录 A.2 官方向量             ech_vectors_test.go
//	B  ClientHello / ECHConfig 编解码         ┐
//	C  填充公式与 32 字节对齐                 ┘ ech_codec_test.go
//	D  端到端往返                             ┐
//	E  篡改与错密钥必然失败                   ┘ ech_proto_test.go
//	F  ech_outer_extensions 压缩与四条 abort  ┐
//	G  接受确认值                             ┘ ech_squeeze_test.go
//
// 实现分三层：hpke_aead.go（RFC 8439）→ hpke.go（RFC 9180）→ ech_wire.go / ech_inner.go /
// ech.go（RFC 9849）。要单独跑某一组，改下面的 groups 即可。
package main

import (
	"fmt"
	"os"
)

func main() {
	groups := []struct {
		name string
		fn   func() int
	}{
		{"RFC 9180 附录 A.2 官方向量", checkHPKEVectors},
		{"ClientHello / ECHConfig 编解码", checkCodec},
		{"填充公式与 32 字节对齐", checkPadding},
		{"端到端往返", checkRoundtrip},
		{"篡改与错密钥必然失败", checkTamper},
		{"ech_outer_extensions 压缩与 4 条 abort", checkOuterExt},
		{"接受确认值", checkConfirmation},
	}
	for _, g := range groups {
		fmt.Printf("  %s: %d checks\n", g.name, g.fn())
	}
	fmt.Printf("ech_demo: %d checks, %d failed\n", checksTotal, checksFailed)
	if checksFailed != 0 {
		os.Exit(1)
	}
}
