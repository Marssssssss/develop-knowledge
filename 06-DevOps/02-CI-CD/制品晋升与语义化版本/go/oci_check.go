// OCI 侧断言: digest 语法/计算、仓库 API、内容发现与晋升闸门。
// 与 python/oci_check.py 对应;harness 与 main() 在 main.go。
package main

import (
	"fmt"
	"strings"
)

const hex64 = "6c3c624b58dbbcd3c0dd82b4c53f04194d1247c6eebdaab7c610cf7d66709b3b"

// ============================ 4. digest ============================

func testDigest() {
	fmt.Println("[4] digest 语法与内容寻址")
	check("sha256 + 64 位小写十六进制合法",
		ValidateDigest("sha256:"+hex64) == "registered", "")
	check("sha512 + 128 位小写十六进制合法",
		ValidateDigest("sha512:"+strings.Repeat("a", 128)) == "registered", "")
	check("官方例子: multihash+base58 语法合法但未注册",
		ValidateDigest("multihash+base58:QmRZxt2b1FVZPNqd8hsiykDL3TdBDeTSPX9Kv46HmX4Gx8")
			== "unregistered", "")
	check("官方例子: sha256+b64u 语法合法但未注册",
		ValidateDigest("sha256+b64u:LCa0a2j_xo_5m0U8HTBBNBNCLXBkg7-g-YpeiGJm564")
			== "unregistered", "")
	check("大写十六进制非法([A-F] MUST NOT be used)",
		ValidateDigest("sha256:"+strings.ToUpper(hex64)) == "invalid", "")
	check("长度不足非法", ValidateDigest("sha256:"+hex64[:63]) == "invalid", "")
	check("算法必须小写", ValidateDigest("SHA256:"+hex64) == "invalid", "")
	check("缺冒号非法", ValidateDigest(hex64) == "invalid", "")
	check("空 encoded 非法", ValidateDigest("sha256:") == "invalid", "")

	check("空内容的 sha256 是官方已知值", MustDigest(nil) ==
		"sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", "")
	check("同一字节在任何仓库都是同一 digest",
		MustDigest([]byte("hello")) == MustDigest([]byte("hello")), "")
	check("verify: 匹配", VerifyDigest([]byte("hello"), MustDigest([]byte("hello"))), "")
	check("verify: 改一字节即失败",
		!VerifyDigest([]byte("hellp"), MustDigest([]byte("hello"))), "")
	d512, err := ComputeDigest([]byte("x"), "sha512")
	check("sha512 摘要更长", err == nil && len(d512) == 135, d512)

	check("ref tag: sha256-<64 位>",
		ReferrersTag("sha256:"+strings.Repeat("a", 64)) == "sha256-"+strings.Repeat("a", 64), "")
	check("ref tag: encoded 截断到 64 位",
		ReferrersTag("sha512:"+strings.Repeat("a", 128)) == "sha512-"+strings.Repeat("a", 64), "")
	longAlg := "test+algorithm+using+algorithm+separators+and+lots+of+characters" +
		"+to+excercise+overall+truncation"
	longEnc := "alsoSome=InTheEncodedSectionToShowHyphenReplacementAndLotsAndLots" +
		"OfCharactersToExcerciseEncodedTruncation"
	check("官方表格第 3 行: 截断 + 非法字符换 -",
		ReferrersTag(longAlg+":"+longEnc) == "test-algorithm-using-algorithm-s"+
			"-alsoSome-InTheEncodedSectionToShowHyphenReplacementAndLotsAndLot", "")
}

// ============================ 5. 仓库 API ============================

func testRegistry() {
	fmt.Println("[5] Pull / Push / 内容发现")
	reg := NewRegistry(true)

	check("非法仓库名(大写)本地即被拒",
		!ValidName("App/UPPER") && reg.PutBlob("App/UPPER", []byte("x"), MustDigest([]byte("x"))).Status == 400, "")
	check("合法仓库名通过", ValidName("demo/app") && !ValidTag("-bad"), "")

	blob := []byte("config-bytes")
	bd := MustDigest(blob)
	check("digest 不匹配 -> 400 DIGEST_INVALID",
		reg.PutBlob("demo/app", blob, MustDigest([]byte("other"))).Header("error") == "DIGEST_INVALID", "")
	check("digest 匹配 -> 201", reg.PutBlob("demo/app", blob, bd).Status == 201, "")

	layer := []byte("layer-bytes")
	ld := MustDigest(layer)
	reg.PutBlob("demo/app", layer, ld)

	orphan := makeManifest(MustDigest([]byte("nope")), ld, nil)
	check("引用不存在的 blob -> MANIFEST_BLOB_UNKNOWN",
		reg.PutManifest("demo/app", orphan, "v9").Header("error") == "MANIFEST_BLOB_UNKNOWN", "")

	data := makeManifest(bd, ld, nil)
	digest := MustDigest(data)
	res := reg.PutManifest("demo/app", data, "latest")
	check("manifest 推送成功 201 且带 digest",
		res.Status == 201 && res.Header("Docker-Content-Digest") == digest, "")
	check("Location 是 manifest URL",
		res.Header("Location") == "/v2/demo/app/manifests/"+digest, res.Header("Location"))

	if got, ok := reg.Resolve("demo/app", "latest"); !ok || got != digest {
		check("tag 可解析成 digest", false, "")
	} else {
		check("tag 可解析成 digest", true, "")
	}
	check("digest 自身也可当 reference", reg.Get("demo/app", digest).Status == 200, "")
	check("GET 成功必须带 Docker-Content-Digest",
		reg.Get("demo/app", "latest").Header("Docker-Content-Digest") == digest, "")
	check("不存在的 tag -> 404", reg.Get("demo/app", "nope").Status == 404, "")
	check("不存在的仓库 -> 404", reg.Get("no/such", "latest").Status == 404, "")
	check("HEAD 返回 Content-Length 与 digest",
		reg.Head("demo/app", "latest").Header("Content-Length") == fmt.Sprint(len(data)), "")

	reordered := []byte(strings.Replace(string(data), `"schemaVersion":2`,
		`"schemaVersion" : 2`, 1))
	check("仓库逐字节保存: 同一逻辑对象的不同字节 = 另一个制品",
		string(reordered) != string(data) && MustDigest(reordered) != digest, "")
	check("按 digest 推送但字节算出来不符 -> 400 DIGEST_INVALID",
		reg.PutManifest("demo/app", reordered, digest).Header("error") == "DIGEST_INVALID", "")
	check("非法 tag(以 - 开头)被拒",
		reg.PutManifest("demo/app", data, "-bad").Header("error") == "NAME_INVALID", "")
	check("tag 长度上限 128",
		reg.PutManifest("demo/app", data, strings.Repeat("t", 129)).Status == 400, "")
}

// ============================ 6. 内容发现 ============================

func testDiscovery() {
	fmt.Println("[6] tags 列表 / referrers")
	reg := NewRegistry(true)
	for _, tag := range []string{"1.9.0", "1.10.0", "latest", "1.2.0"} {
		pushImage(reg, "demo/app", tag)
	}
	res := reg.TagsList("demo/app", ListOptions{})
	check("tags 按 ASCIIbetical 序(1.10.0 在 1.9.0 之前)",
		eqS(decodeTags(res.Raw), []string{"1.10.0", "1.2.0", "1.9.0", "latest"}),
		fmt.Sprint(decodeTags(res.Raw)))

	res = reg.TagsList("demo/app", ListOptions{Paged: true, N: 2})
	check("n=2 分页返回 2 条并带 rel=\"next\"",
		len(decodeTags(res.Raw)) == 2 && strings.Contains(res.Header("Link"), `rel="next"`), "")
	check("Link 的 last 是页内最后一条",
		strings.HasSuffix(res.Header("Link"), `last=1.2.0>; rel="next"`), res.Header("Link"))
	check("last 参数从指定 tag 之后继续",
		eqS(decodeTags(reg.TagsList("demo/app", ListOptions{Paged: true, N: 5, Last: "1.2.0"}).Raw),
			[]string{"1.9.0", "latest"}), "")
	res = reg.TagsList("demo/app", ListOptions{Paged: true, N: 0})
	check("n=0 返回空列表且不带 Link",
		len(decodeTags(res.Raw)) == 0 && res.Header("Link") == "", "")
	check("不存在的仓库 tags 列表 404",
		reg.TagsList("no/such", ListOptions{}).Status == 404, "")

	subject := pushImage(reg, "demo/app", "1.0.0")
	sbom := []byte(`{"format":"spdx"}`)
	sd := MustDigest(sbom)
	reg.PutBlob("demo/app", sbom, sd)
	referrer := makeManifest(sd, sd, map[string]interface{}{
		"subject": map[string]interface{}{"mediaType": manifestMediaType,
			"digest": subject, "size": float64(10)},
		"artifactType": "application/vnd.example.sbom.v1"})
	check("subject 指向不存在的 manifest 时仍可推送",
		reg.PutManifest("demo/app", referrer, "").Status == 201, "")

	res = reg.Referrers("demo/app", subject, false)
	idx := decodeIndex(res.Raw)
	check("referrers 返回 image index",
		res.Status == 200 && res.Header("Content-Type") == indexMediaType, fmt.Sprint(res.Status))
	if len(idx) == 1 {
		check("index 里带 artifactType 与 digest",
			idx[0]["artifactType"] == "application/vnd.example.sbom.v1" &&
				idx[0]["digest"] == MustDigest(referrer), fmt.Sprint(idx[0]))
	} else {
		check("index 里带 artifactType 与 digest", false, fmt.Sprint(idx))
	}
	check("无匹配时返回空 index",
		len(decodeIndex(reg.Referrers("demo/app", MustDigest([]byte("none")), false).Raw)) == 0, "")
	check("非法 digest 语法 -> 400",
		reg.Referrers("demo/app", "sha256:XYZ", false).Status == 400, "")

	noAPI := NewRegistry(false)
	subject2 := pushImage(noAPI, "demo/app", "1.0.0")
	check("不支持 referrers API 的仓库返回 404",
		noAPI.Referrers("demo/app", subject2, false).Status == 404, "")
	indexBytes := mustJSON(map[string]interface{}{"schemaVersion": 2,
		"mediaType": indexMediaType, "manifests": []interface{}{}})
	fbTag := ReferrersTag(subject2)
	check("回退 tag 形如 <算法>-<encoded>",
		fbTag == "sha256-"+strings.TrimPrefix(subject2, "sha256:"), "")
	check("回退 tag 本身是合法 tag",
		ValidTag(fbTag) && noAPI.PutManifest("demo/app", indexBytes, fbTag).Status == 201, "")
	res = noAPI.Referrers("demo/app", subject2, true)
	check("客户端回退到 referrers tag 后能拿到 index",
		res.Status == 200 && len(decodeIndex(res.Raw)) == 0, fmt.Sprint(res.Status))
}

// ============================ 7. 晋升闸门 ============================

func testPromotion() {
	fmt.Println("[7] 制品晋升")
	reg := NewRegistry(true)
	for _, v := range []string{"1.2.0", "1.3.0-rc.1", "1.4.0", "latest"} {
		pushImage(reg, "demo/app", v)
	}

	d, _ := Promote(reg, "demo/app", "1.2.0", "prod", Policy{Channel: "prod"})
	check("稳定版可以晋升到 prod", d.OK, d.Reason)
	if got, ok := reg.Resolve("demo/app", "prod"); !ok || got != d.Digest {
		check("晋升后 tag 指向同一 digest", false, "")
	} else {
		check("晋升后 tag 指向同一 digest", true, "")
	}
	check("晋升只移动指针: 字节未变", d.BytesUnchanged, "")
	check("晋升后 digest 仍然等于内容摘要", d.DigestUnchanged, "")

	d, _ = Promote(reg, "demo/app", "1.2.0", "prod", Policy{Channel: "prod"})
	check("重复晋升同一 digest 是幂等的",
		!d.OK && strings.Contains(d.Reason, "幂等"), d.Reason)

	d, _ = Promote(reg, "demo/app", "1.3.0-rc.1", "prod", Policy{Channel: "prod"})
	check("预发布默认不得进 prod 通道", !d.OK, d.Reason)
	d, _ = Promote(reg, "demo/app", "1.3.0-rc.1", "prod",
		Policy{Channel: "prod", AllowPrerelease: true})
	check("显式放开后预发布可以进", d.OK, d.Reason)

	d, _ = PromotionDecision(reg, "demo/app", "1.2.0", "prod",
		Policy{Channel: "prod", CurrentVersion: "1.3.0"})
	check("通道当前版本更高时拒绝降级",
		!d.OK && strings.Contains(d.Reason, "拒绝降级"), d.Reason)
	d, _ = PromotionDecision(reg, "demo/app", "1.4.0", "prod",
		Policy{Channel: "prod", CurrentVersion: "1.3.0"})
	check("通道当前版本更低时放行", d.OK, d.Reason)
	if _, err := PromotionDecision(reg, "demo/app", "1.2.0", "prod",
		Policy{Channel: "prod", CurrentVersion: "v1.3.0"}); err != nil {
		check("CurrentVersion 非法时显式报错", true, "")
	} else {
		check("CurrentVersion 非法时显式报错", false, "")
	}

	d, _ = Promote(reg, "demo/app", "9.9.9", "prod", Policy{Channel: "prod"})
	check("来源不存在 -> 拒绝", !d.OK && strings.Contains(d.Reason, "404"), d.Reason)
	d, _ = PromotionDecision(reg, "demo/app", "latest", "prod", Policy{Channel: "prod"})
	check("非版本 tag 无法做版本闸门",
		!d.OK && strings.Contains(d.Reason, "语义化版本"), d.Reason)

	one := pushImage(reg, "demo/app", "1.5.0")
	moved, _ := CopyAcross(reg, "demo/app", "1.5.0", "mirror/app", "1.5.0")
	check("跨仓库搬运成功", moved.OK, moved.Reason)
	check("搬运后 digest 不变(内容寻址的核心收益)", moved.Digest == one, moved.Digest)
	if dst, ok := reg.Repos["mirror/app"]; ok {
		check("镜像仓库里能按 digest 拉到同一字节",
			string(dst.Manifests[one]) == string(reg.Repos["demo/app"].Manifests[one]), "")
	} else {
		check("镜像仓库里能按 digest 拉到同一字节", false, "")
	}
}
