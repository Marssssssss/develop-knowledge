// OCI Distribution Spec 的 registry 侧模型（Python 模型的 Go 复刻）。
//
// 语义对照 opencontainers/distribution-spec spec.md：
//
//	§Endpoints（end-1 .. end-14）、§Error Codes（code-1 .. code-14）
//	§Pushing blobs monolithically / in chunks、§Mounting、§Listing Tags
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"regexp"
	"sort"
	"strings"
)

var contentRangeRe = regexp.MustCompile(`^[0-9]+-[0-9]+$`)

// RegistryError 对应规范 §Error Codes 的 JSON 错误体。
type RegistryError struct {
	Status  int
	Code    string
	Message string
	Detail  map[string]string
}

func (e *RegistryError) Error() string { return e.Code }

func (e *RegistryError) Body() map[string]interface{} {
	err := map[string]interface{}{"code": e.Code, "message": e.Message}
	if e.Detail != nil {
		err["detail"] = e.Detail
	}
	return map[string]interface{}{"errors": []interface{}{err}}
}

func regErr(status int, code, msg string) *RegistryError {
	return &RegistryError{Status: status, Code: code, Message: msg}
}

// Response 是最小 HTTP 响应模型。
type Response struct {
	Status  int
	Headers map[string]string
	Body    interface{}
}

func digestOf(data []byte) string {
	sum := sha256.Sum256(data)
	return "sha256:" + hex.EncodeToString(sum[:])
}

type UploadSession struct {
	UUID string
	Name string
	Data []byte
}

func (s *UploadSession) Offset() int { return len(s.Data) }

type Repo struct {
	Manifests map[string]map[string]interface{}
	Tags      map[string]string
	Blobs     map[string][]byte
}

type Registry struct {
	Repos      map[string]*Repo
	Uploads    map[string]*UploadSession
	Delete     bool
	nextUUID   int
}

func NewRegistry(deleteEnabled bool) *Registry {
	return &Registry{Repos: map[string]*Repo{}, Uploads: map[string]*UploadSession{},
		Delete: deleteEnabled}
}

func (rg *Registry) EnsureRepo(name string) *Repo {
	if _, ok := rg.Repos[name]; !ok {
		rg.Repos[name] = &Repo{Manifests: map[string]map[string]interface{}{},
			Tags: map[string]string{}, Blobs: map[string][]byte{}}
	}
	return rg.Repos[name]
}

// Repo 对应 end-1..end-12 共用的仓库定位：不存在的仓库一律 404。
func (rg *Registry) Repo(name string) (*Repo, *RegistryError) {
	if r, ok := rg.Repos[name]; ok {
		return r, nil
	}
	return nil, regErr(404, "NAME_UNKNOWN", "repository name not known to registry")
}

func (rg *Registry) GetV2() *Response { return &Response{Status: 200, Headers: map[string]string{}} }

// end-2：GET /v2/<name>/blobs/<digest>
func (rg *Registry) GetBlob(name, digest string) (*Response, *RegistryError) {
	r, err := rg.Repo(name)
	if err != nil {
		return nil, err
	}
	data, ok := r.Blobs[digest]
	if !ok {
		return nil, regErr(404, "BLOB_UNKNOWN", "blob unknown to registry")
	}
	return &Response{Status: 200, Headers: map[string]string{
		"Docker-Content-Digest": digest, "Content-Length": fmt.Sprint(len(data))},
		Body: data}, nil
}

// end-4a/4b：POST /v2/<name>/blobs/uploads/
func (rg *Registry) PostUpload(name, mount, from string) (*Response, *RegistryError) {
	if mount != "" && from != "" {
		if src, ok := rg.Repos[from]; ok {
			if blob, ok := src.Blobs[mount]; ok {
				rg.EnsureRepo(name).Blobs[mount] = blob
				return &Response{Status: 201, Headers: map[string]string{
					"Location": "/v2/" + name + "/blobs/" + mount}}, nil
			}
		}
	}
	rg.nextUUID++
	s := &UploadSession{UUID: fmt.Sprintf("u%d", rg.nextUUID), Name: name}
	rg.Uploads[s.UUID] = s
	return &Response{Status: 202, Headers: map[string]string{
		"Location": "/v2/" + name + "/blobs/uploads/" + s.UUID,
		"Docker-Upload-UUID": s.UUID, "Range": "0-0"}}, nil
}

func (rg *Registry) session(uuid string) (*UploadSession, *RegistryError) {
	if s, ok := rg.Uploads[uuid]; ok {
		return s, nil
	}
	return nil, regErr(404, "BLOB_UPLOAD_UNKNOWN", "blob upload unknown to registry")
}

// end-5：PATCH，range 必须连续且首个必须从 0 起。
func (rg *Registry) PatchUpload(uuid string, data []byte, contentRange string) (*Response, *RegistryError) {
	s, err := rg.session(uuid)
	if err != nil {
		return nil, err
	}
	if contentRange != "" {
		if !contentRangeRe.MatchString(contentRange) {
			return nil, regErr(416, "BLOB_UPLOAD_INVALID", "bad content range")
		}
		parts := strings.SplitN(contentRange, "-", 2)
		start := atoi(parts[0])
		if s.Offset() == 0 && start != 0 {
			return nil, regErr(416, "BLOB_UPLOAD_INVALID",
				"the first chunk's range must begin with 0")
		}
		if start != s.Offset() {
			return nil, regErr(416, "BLOB_UPLOAD_INVALID", "chunk out of order")
		}
		if len(data) != atoi(parts[1])-start+1 {
			return nil, regErr(416, "BLOB_UPLOAD_INVALID",
				"content-length does not match range")
		}
	}
	s.Data = append(s.Data, data...)
	return &Response{Status: 202, Headers: map[string]string{
		"Range": fmt.Sprintf("0-%d", s.Offset()-1),
		"Docker-Upload-UUID": s.UUID}}, nil
}

// end-6：PUT ?digest=<digest> 关闭会话，digest 不匹配即 DIGEST_INVALID。
func (rg *Registry) PutUpload(uuid, digest string, data []byte, contentRange string) (*Response, *RegistryError) {
	s, err := rg.session(uuid)
	if err != nil {
		return nil, err
	}
	if len(data) > 0 {
		if _, err := rg.PatchUpload(uuid, data, contentRange); err != nil {
			return nil, err
		}
	}
	got := digestOf(s.Data)
	if got != digest {
		return nil, &RegistryError{Status: 400, Code: "DIGEST_INVALID",
			Message: "provided digest did not match uploaded content",
			Detail:  map[string]string{"provided": digest, "computed": got}}
	}
	rg.EnsureRepo(s.Name).Blobs[got] = s.Data
	delete(rg.Uploads, uuid)
	return &Response{Status: 201, Headers: map[string]string{
		"Location": "/v2/" + s.Name + "/blobs/" + got,
		"Docker-Content-Digest": got}}, nil
}

func (rg *Registry) DeleteUpload(uuid string) (*Response, *RegistryError) {
	if _, err := rg.session(uuid); err != nil {
		return nil, err
	}
	delete(rg.Uploads, uuid)
	return &Response{Status: 204}, nil
}

// end-8：ASCIIbetical 排序 + n/last 分页，last 非包含。
func (rg *Registry) ListTags(name string, n int, last string) (*Response, *RegistryError) {
	r, err := rg.Repo(name)
	if err != nil {
		return nil, err
	}
	tags := make([]string, 0, len(r.Tags))
	for t := range r.Tags {
		tags = append(tags, t)
	}
	sort.Strings(tags)
	if last != "" {
		kept := tags[:0]
		for _, t := range tags {
			if t > last {
				kept = append(kept, t)
			}
		}
		tags = kept
	}
	headers := map[string]string{}
	page := tags
	if n > 0 {
		if n < len(tags) {
			page = tags[:n]
			headers["Link"] = fmt.Sprintf("</v2/%s/tags/list?n=%d&last=%s>; rel=\"next\"",
				name, len(tags)-n, page[len(page)-1])
		}
	} else if n == 0 {
		page = nil
	}
	return &Response{Status: 200, Headers: headers,
		Body: map[string]interface{}{"name": name, "tags": page}}, nil
}

// end-10：DELETE /v2/<name>/blobs/<digest>
func (rg *Registry) DeleteBlob(name, digest string) (*Response, *RegistryError) {
	r, err := rg.Repo(name)
	if err != nil {
		return nil, err
	}
	if !rg.Delete {
		return nil, regErr(405, "UNSUPPORTED", "the operation is unsupported")
	}
	if _, ok := r.Blobs[digest]; !ok {
		return nil, regErr(404, "BLOB_UNKNOWN", "blob unknown to registry")
	}
	delete(r.Blobs, digest)
	return &Response{Status: 202}, nil
}

func atoi(s string) int {
	n := 0
	fmt.Sscanf(s, "%d", &n)
	return n
}
