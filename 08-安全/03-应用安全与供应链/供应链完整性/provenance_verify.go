// SLSA v1.0 Provenance 的生成与验证（Go 版）。
// 重点是**验证规则**：subject 摘要绑定、signer-builder 配对、builder.id 决定级别、
// externalParameters 必须验证、internalParameters 无需验证、扩展字段必须忽略。
package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"sort"
	"strings"
)

const (
	predicateType = "https://slsa.dev/provenance/v1"
	statementType = "https://in-toto.io/Statement/v1"
)

// builder.id -> 该构建平台声明的 SLSA Build level。
// 规范：builder.id "is intended to be the sole determiner of the SLSA Build level"。
var builderLevels = map[string]int{
	"github-hosted":  2,
	"cloud-build":    3,
	"evil-builder":   0,
}

// 消费者只接受特定的 signer-builder 配对。
var acceptedPairs = map[string]bool{
	"github|github-hosted": true,
	"google|cloud-build":   true,
}

// 我们对正常 externalParameters 的预期（不可信，必须下游验证）。
var expectedExternal = map[string]bool{"repository": true, "ref": true}

// Prov 是一份 provenance；未识别字段用 Extra* 显式表示，便于演示「必须忽略」。
type Prov struct {
	SubjectName    string
	SubjectDigest  string
	PredicateType  string
	BuildType      string
	External       map[string]string
	Internal       map[string]string
	Deps           []string
	BuilderID      string
	InvocationID   string
	ExtraPredicate []string // 未识别的 predicate 字段（扩展）
	ExtraTop       []string // 未识别的顶层字段
	ClaimedLevel   int      // x_slsaBuildLevel 扩展；-1 表示不存在
}

type Envelope struct {
	KeyID     string
	Signature string
	Payload   string // 规范化的 statement
}

func canonical(p Prov) string {
	var b strings.Builder
	b.WriteString(p.SubjectName + "|" + p.SubjectDigest + "|" + p.PredicateType)
	b.WriteString("|" + p.BuildType + "|" + p.BuilderID + "|" + p.InvocationID)
	keys := []string{}
	for k := range p.External {
		keys = append(keys, k)
	}
	sort.Strings(keys) // 键序无关，签名才稳定
	for _, k := range keys {
		b.WriteString("|e:" + k + "=" + p.External[k])
	}
	keys = keys[:0]
	for k := range p.Internal {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		b.WriteString("|i:" + k + "=" + p.Internal[k])
	}
	for _, d := range p.Deps {
		b.WriteString("|d:" + d)
	}
	return b.String()
}

func sign(p Prov, key []byte, keyID string) Envelope {
	mac := hmac.New(sha256.New, key)
	mac.Write([]byte(canonical(p)))
	return Envelope{KeyID: keyID, Signature: hex.EncodeToString(mac.Sum(nil)),
		Payload: canonical(p)}
}

func verifySignature(e Envelope, key []byte) bool {
	mac := hmac.New(sha256.New, key)
	mac.Write([]byte(e.Payload))
	return hmac.Equal(mac.Sum(nil), mustHex(e.Signature))
}

func mustHex(s string) []byte {
	b, err := hex.DecodeString(s)
	if err != nil {
		return []byte("\x00invalid")
	}
	return b
}

// verify 返回 (决定, 理由列表)
func verify(p Prov, e Envelope, key []byte, artifact []byte, minLevel int) (string, []string) {
	var reasons []string
	if !verifySignature(e, key) {
		return "DENY", []string{"签名校验失败"}
	}
	sum := sha256.Sum256(artifact)
	if p.SubjectDigest != hex.EncodeToString(sum[:]) {
		return "DENY", []string{"subject 摘要与制品不匹配（制品在构建后被替换）"}
	}
	if p.PredicateType != predicateType {
		return "DENY", []string{"predicateType 不是 SLSA provenance v1"}
	}
	// 1) signer-builder 配对
	if !acceptedPairs[e.KeyID+"|"+p.BuilderID] {
		return "DENY", []string{fmt.Sprintf(
			"不接受该 signer-builder 配对: signer=%s builder=%s", e.KeyID, p.BuilderID)}
	}
	// 2) builder.id 唯一决定级别
	level := builderLevels[p.BuilderID]
	if level < minLevel {
		msg := fmt.Sprintf("builder.id 声明的级别 %d 低于要求 %d", level, minLevel)
		if p.ClaimedLevel >= 0 {
			msg += fmt.Sprintf("；扩展字段 x_slsaBuildLevel=%d 不得用于提升级别", p.ClaimedLevel)
		}
		return "DENY", []string{msg}
	}
	// 3) externalParameters 不可信 → 拒绝未预期字段
	unexpected := []string{}
	for k := range p.External {
		if !expectedExternal[k] {
			unexpected = append(unexpected, k)
		}
	}
	if len(unexpected) > 0 {
		sort.Strings(unexpected)
		return "DENY", []string{"externalParameters 出现未预期字段: " + strings.Join(unexpected, ",")}
	}
	// 4) internalParameters 由可信平台设置 → 规范明说无需验证
	reasons = append(reasons, "internalParameters 由受信任平台设置，按规范不校验")
	// 5) 未识别字段必须忽略
	if len(p.ExtraTop) > 0 {
		reasons = append(reasons, "忽略未识别顶层字段: "+strings.Join(p.ExtraTop, ","))
	}
	if len(p.ExtraPredicate) > 0 {
		reasons = append(reasons, "忽略未识别 predicate 字段（扩展）: "+strings.Join(p.ExtraPredicate, ","))
	}
	if p.ClaimedLevel >= 0 {
		reasons = append(reasons, fmt.Sprintf(
			"扩展字段 x_slsaBuildLevel=%d 被忽略：级别只能由 builder.id 决定", p.ClaimedLevel))
	}
	return "ALLOW", reasons
}

var key = []byte("demo-signing-key")

func goodProv() Prov {
	return Prov{
		SubjectName: "hello-world",
		PredicateType: predicateType,
		BuildType:    "https://slsa-framework.github.io/github-actions-buildtypes/workflow/v1",
		External: map[string]string{
			"repository": "https://github.com/octocat/hello-world",
			"ref":        "refs/heads/main"},
		Internal:     map[string]string{"runnerArch": "X64"},
		Deps:         []string{"gitCommit:7fd1a60b01f91b314f59955a4e4d4e80d8edf11d"},
		BuilderID:    "github-hosted",
		InvocationID: "inv-0001",
		ClaimedLevel: -1,
	}
}

type scenario struct {
	name     string
	prov     Prov
	sigKeyID string
	artifact []byte
	minLevel int
	want     string
}

func scenarios() []scenario {
	artifact := []byte("binary-v1.0.0")
	base := goodProv()
	base.SubjectDigest = digestOf(artifact)
	out := []scenario{
		{"S1 正常", base, "github", artifact, 2, "ALLOW"},
		{"S2 制品被替换", base, "github", []byte("binary-v1.0.0-with-backdoor"), 2, "DENY"},
	}
	evil := goodProv()
	evil.BuilderID = "evil-builder"
	evil.SubjectDigest = digestOf(artifact)
	out = append(out, scenario{"S3 未知 builder", evil, "github", artifact, 2, "DENY"})

	gcb := goodProv()
	gcb.BuilderID = "cloud-build"
	gcb.SubjectDigest = digestOf(artifact)
	out = append(out, scenario{"S4 signer-builder 不匹配", gcb, "github", artifact, 2, "DENY"})

	inj := goodProv()
	inj.SubjectDigest = digestOf(artifact)
	inj.External = map[string]string{
		"repository": base.External["repository"], "ref": base.External["ref"],
		"entryPoint": "attacker-supplied.yml"}
	out = append(out, scenario{"S5 externalParameters 有意外字段", inj, "github", artifact, 2, "DENY"})

	withExt := goodProv()
	withExt.SubjectDigest = digestOf(artifact)
	withExt.ExtraPredicate = []string{"x_customHint"}
	out = append(out, scenario{"S6 未知扩展字段被忽略", withExt, "github", artifact, 2, "ALLOW"})

	lvl := goodProv()
	lvl.SubjectDigest = digestOf(artifact)
	lvl.ExtraPredicate = []string{"x_slsaBuildLevel"}
	lvl.ClaimedLevel = 3
	out = append(out, scenario{"S7 扩展自称 L3 但要求 L3", lvl, "github", artifact, 3, "DENY"})
	return out
}

func digestOf(b []byte) string {
	s := sha256.Sum256(b)
	return hex.EncodeToString(s[:])
}

func main() {
	fail := 0
	for _, sc := range scenarios() {
		e := sign(sc.prov, key, sc.sigKeyID)
		got, reasons := verify(sc.prov, e, key, sc.artifact, sc.minLevel)
		flag := "OK "
		if got != sc.want {
			flag = "BAD"
			fail++
		}
		fmt.Printf("%s %-34s -> %-5s (want %s)\n", flag, sc.name, got, sc.want)
		for _, r := range reasons {
			fmt.Printf("      · %s\n", r)
		}
	}
	// 同一份证明，把要求级别从 2 提到 3 → 结论反转
	artifact := []byte("binary-v1.0.0")
	g := goodProv()
	g.SubjectDigest = digestOf(artifact)
	e := sign(g, key, "github")
	if got, _ := verify(g, e, key, artifact, 2); got != "ALLOW" {
		fmt.Println("FAIL 要求 L2 应通过")
		fail++
	}
	if got, _ := verify(g, e, key, artifact, 3); got != "DENY" {
		fmt.Println("FAIL 要求 L3 应拒绝")
		fail++
	}
	if verifySignature(Envelope{KeyID: "github", Signature: "deadbeef",
		Payload: canonical(g)}, key) {
		fmt.Println("FAIL 伪造签名不应通过")
		fail++
	}
	if fail > 0 {
		fmt.Printf("FAILED %d\n", fail)
		os.Exit(1)
	}
	fmt.Println("all checks passed")
}
