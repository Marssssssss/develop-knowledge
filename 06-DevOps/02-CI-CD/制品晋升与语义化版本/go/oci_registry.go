// OCI 制品分发语义(Go 对照实现): digest 语法与计算、最小仓库 API、referrers。
//
// 权威依据(本 demo 实读原文):
//   - OCI Distribution Specification(spec.md): Pull/Push/Content Discovery/
//     Content Management 四类 API、`<tag-or-digest>` 与 `<name>` 正则、
//     tags 的 ASCIIbetical 序与 n/last 分页、referrers API 与回退 tag 方案。
//   - OCI Image Spec(descriptor.md): digest 语法、内容寻址、sha256/sha512 的
//     encoded 必须是小写十六进制([A-F] MUST NOT be used)。
package main

import (
	"crypto/sha256"
	"crypto/sha512"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"regexp"
	"strings"
)

const (
	manifestMediaType = "application/vnd.oci.image.manifest.v1+json"
	indexMediaType    = "application/vnd.oci.image.index.v1+json"
)

var (
	digestGrammar = regexp.MustCompile(`^[a-z0-9]+(?:[+._-][a-z0-9]+)*:[a-zA-Z0-9=_-]+$`)
	sha256Encoded = regexp.MustCompile(`^[a-f0-9]{64}$`)
	sha512Encoded = regexp.MustCompile(`^[a-f0-9]{128}$`)
	tagRE         = regexp.MustCompile(`^[a-zA-Z0-9_][a-zA-Z0-9._-]{0,127}$`)
	nameRE        = regexp.MustCompile(`^[a-z0-9]+((\.|_|__|-+)[a-z0-9]+)*` +
		`(/[a-z0-9]+((\.|_|__|-+)[a-z0-9]+)*)*$`)
)

// Result 是模拟的 HTTP 响应: 状态码 + 头 + 原始字节体。
type Result struct {
	Status  int
	Headers map[string]string
	Raw     []byte
}

// Header 是取头的简写(nil map 安全)。
func (r Result) Header(k string) string { return r.Headers[k] }

// ValidateDigest 返回 registered / unregistered / invalid;
// 未注册算法只要符合语法就放行(规范: SHOULD allow digests with unrecognized algorithms)。
func ValidateDigest(d string) string {
	if !digestGrammar.MatchString(d) {
		return "invalid"
	}
	i := strings.Index(d, ":")
	alg, encoded := d[:i], d[i+1:]
	switch alg {
	case "sha256":
		if sha256Encoded.MatchString(encoded) {
			return "registered"
		}
		return "invalid"
	case "sha512":
		if sha512Encoded.MatchString(encoded) {
			return "registered"
		}
		return "invalid"
	}
	return "unregistered"
}

// ComputeDigest 实现 '<alg>:' + Encode(H(C))。
func ComputeDigest(data []byte, algorithm string) (string, error) {
	switch algorithm {
	case "sha256":
		sum := sha256.Sum256(data)
		return "sha256:" + hex.EncodeToString(sum[:]), nil
	case "sha512":
		sum := sha512.Sum512(data)
		return "sha512:" + hex.EncodeToString(sum[:]), nil
	}
	return "", fmt.Errorf("不支持的摘要算法: %s", algorithm)
}

// MustDigest 供断言使用。
func MustDigest(data []byte) string {
	d, err := ComputeDigest(data, "sha256")
	if err != nil {
		panic(err)
	}
	return d
}

// VerifyDigest 重新计算摘要并与给定 digest 比对。
func VerifyDigest(data []byte, digest string) bool {
	if ValidateDigest(digest) == "invalid" {
		return false
	}
	i := strings.Index(digest, ":")
	got, err := ComputeDigest(data, digest[:i])
	return err == nil && got == digest
}

// ReferrersTag 实现 Referrers Tag 方案: 算法截断 32 + '-' + encoded 截断 64,
// 非法字符替换为 '-'。
func ReferrersTag(subjectDigest string) string {
	i := strings.Index(subjectDigest, ":")
	alg, encoded := subjectDigest[:i], subjectDigest[i+1:]
	if len(alg) > 32 {
		alg = alg[:32]
	}
	if len(encoded) > 64 {
		encoded = encoded[:64]
	}
	raw := alg + "-" + encoded
	return strings.Map(func(r rune) rune {
		if (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') ||
			(r >= '0' && r <= '9') || r == '.' || r == '_' || r == '-' {
			return r
		}
		return '-'
	}, raw)
}

// ValidName / ValidTag 暴露正则, 便于调用方(客户端)在发送前做本地校验。
func ValidName(n string) bool { return nameRE.MatchString(n) }

// ValidTag 校验 tag 形态(至多 128 字符)。
func ValidTag(t string) bool { return tagRE.MatchString(t) }

// Repo 是单个仓库命名空间。
type Repo struct {
	Blobs     map[string]bool
	Manifests map[string][]byte
	Tags      map[string]string
}

// Registry 是按规范语义实现的最小仓库(不做真实网络传输)。
type Registry struct {
	SupportsReferrers bool
	Blobs             map[string][]byte
	Repos             map[string]*Repo
}

// NewRegistry 建一个空仓库。
func NewRegistry(supportsReferrers bool) *Registry {
	return &Registry{SupportsReferrers: supportsReferrers,
		Blobs: map[string][]byte{}, Repos: map[string]*Repo{}}
}

func (r *Registry) repo(name string, create bool) (*Repo, error) {
	if !nameRE.MatchString(name) {
		return nil, fmt.Errorf("非法的仓库名: %q", name)
	}
	if p, ok := r.Repos[name]; ok {
		return p, nil
	}
	if !create {
		return nil, nil
	}
	p := &Repo{Blobs: map[string]bool{}, Manifests: map[string][]byte{},
		Tags: map[string]string{}}
	r.Repos[name] = p
	return p, nil
}

// PutBlob 对应 PUT <blob-push-location>?digest=<digest>。
func (r *Registry) PutBlob(name string, data []byte, digest string) Result {
	repo, err := r.repo(name, true)
	if err != nil {
		return errResult(400, "NAME_INVALID")
	}
	if ValidateDigest(digest) == "invalid" {
		return errResult(400, "DIGEST_INVALID")
	}
	if !VerifyDigest(data, digest) {
		return errResult(400, "DIGEST_INVALID")
	}
	repo.Blobs[digest] = true
	if _, ok := r.Blobs[digest]; !ok {
		r.Blobs[digest] = data
	}
	return Result{Status: 201, Headers: map[string]string{
		"Location": "/v2/" + name + "/blobs/" + digest}}
}

// PutManifest 对应 PUT /v2/<name>/manifests/<tag-or-digest>。
// 仓库必须逐字节保存原始字节(不重新序列化), 因此 digest 不变 == 内容未变。
func (r *Registry) PutManifest(name string, data []byte, reference string) Result {
	repo, err := r.repo(name, true)
	if err != nil {
		return errResult(400, "NAME_INVALID")
	}
	digest := MustDigest(data)
	refIsDigest := reference != ""
	if refIsDigest {
		refIsDigest = strings.Contains(reference, ":") // tag 正则不含冒号
		if refIsDigest {
			if reference != digest {
				return errResult(400, "DIGEST_INVALID")
			}
		} else if !tagRE.MatchString(reference) {
			return errResult(400, "NAME_INVALID")
		}
	}
	parsed := map[string]interface{}{}
	if err := json.Unmarshal(data, &parsed); err != nil {
		return errResult(400, "MANIFEST_INVALID")
	}
	for _, d := range referencedDigests(parsed) {
		if !repo.Blobs[d] {
			if _, ok := repo.Manifests[d]; !ok {
				return errResult(400, "MANIFEST_BLOB_UNKNOWN")
			}
		}
	}
	repo.Manifests[digest] = data
	if reference != "" && !refIsDigest {
		repo.Tags[reference] = digest
	}
	return Result{Status: 201, Headers: map[string]string{
		"Location":              "/v2/" + name + "/manifests/" + digest,
		"Docker-Content-Digest": digest}}
}

// Get 对应 GET /v2/<name>/manifests/<tag-or-digest>。
func (r *Registry) Get(name, ref string) Result {
	digest, ok := r.Resolve(name, ref)
	if !ok {
		return Result{Status: 404, Headers: map[string]string{}}
	}
	repo, _ := r.repo(name, false)
	return Result{Status: 200, Raw: repo.Manifests[digest],
		Headers: map[string]string{"Docker-Content-Digest": digest,
			"Content-Type": manifestMediaType}}
}

// Head 只回头: 200 必须带 Docker-Content-Digest 与 Content-Length。
func (r *Registry) Head(name, ref string) Result {
	got := r.Get(name, ref)
	if got.Status != 200 {
		return Result{Status: got.Status, Headers: map[string]string{}}
	}
	got.Headers["Content-Length"] = fmt.Sprint(len(got.Raw))
	return Result{Status: 200, Headers: got.Headers}
}

// Resolve 把 <tag-or-digest> 解析成 digest(tag 可变, digest 不可变)。
func (r *Registry) Resolve(name, ref string) (string, bool) {
	repo, err := r.repo(name, false)
	if err != nil || repo == nil {
		return "", false
	}
	if _, ok := repo.Manifests[ref]; ok {
		return ref, true
	}
	d, ok := repo.Tags[ref]
	return d, ok
}

// SetTag 把 tag 指向已有 digest —— 晋升的本质, 不产生任何新字节。
func (r *Registry) SetTag(name, tag, digest string) bool {
	repo, err := r.repo(name, false)
	if err != nil || repo == nil {
		return false
	}
	if _, ok := repo.Manifests[digest]; !ok {
		return false
	}
	repo.Tags[tag] = digest
	return true
}
