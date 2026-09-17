// 值规整与摘要: core/Quantity 与 knownTypeFields 的去噪, 以及内容寻址用的摘要。
// 拆自 argocd_diff.go, 以满足 OPTIMIZATION.md §1.1 的"单源文件 ≤ 300 行"。
package main

import (
	"crypto/sha256"
	"crypto/sha512"
	"encoding/hex"
	"fmt"
	"strconv"
	"strings"
)

// CanonicalizeQuantity: 100m 与 0.1 是同一个量;非数字字符串原样返回。
// 本 demo 只规整十进制写法, 1Gi / 1e3 这类官方 Quantity 后缀不展开(原样返回)。
func CanonicalizeQuantity(v interface{}) interface{} {
	s, ok := v.(string)
	if !ok {
		return v
	}
	if strings.HasSuffix(s, "m") && isDecimal(strings.TrimSuffix(s, "m")) {
		f, err := strconv.ParseFloat(strings.TrimSuffix(s, "m"), 64)
		if err == nil {
			return f / 1000.0
		}
	}
	if isDecimal(s) || isSignedDecimal(s) {
		f, err := strconv.ParseFloat(s, 64)
		if err == nil {
			return f
		}
	}
	return v
}

// isDecimal 只认 [0-9]+(\.[0-9]*)? 形态(与 Python 版口径一致, 不接受正负号)。
func isDecimal(s string) bool {
	if s == "" {
		return false
	}
	dot := false
	for _, r := range s {
		switch {
		case r >= '0' && r <= '9':
		case r == '.' && !dot:
			dot = true
		default:
			return false
		}
	}
	return true
}

func isSignedDecimal(s string) bool {
	if strings.HasPrefix(s, "-") || strings.HasPrefix(s, "+") {
		return isDecimal(s[1:])
	}
	return false
}

var knownTypePaths = map[string]map[string]string{
	"argoproj.io/Rollout": {"spec.template.spec": "core/v1/PodSpec"},
}

// CanonicalizeKnownTypes 把指定字段按 Kubernetes 内建类型规整, 消除自定义
// marshaler 造成的假漂移;未登记的组合直接报错(官方要求 core/v1/PodSpec)。
func CanonicalizeKnownTypes(obj map[string]interface{}, groupKind, fieldPath string) error {
	if knownTypePaths[groupKind][fieldPath] != "core/v1/PodSpec" {
		return fmt.Errorf("未登记的 knownTypeFields: %s %s", groupKind, fieldPath)
	}
	cur := obj
	for _, p := range strings.Split(fieldPath, ".") {
		child, ok := cur[p].(map[string]interface{})
		if !ok {
			return nil
		}
		cur = child
	}
	containers, _ := cur["containers"].([]interface{})
	for _, c := range containers {
		cm, ok := c.(map[string]interface{})
		if !ok {
			continue
		}
		res, ok := cm["resources"].(map[string]interface{})
		if !ok {
			continue
		}
		req, ok := res["requests"].(map[string]interface{})
		if !ok {
			continue
		}
		for k, val := range req {
			req[k] = CanonicalizeQuantity(val)
		}
	}
	return nil
}

// ContentDigest 对应 OCI/镜像摘要的 "算法:十六进制" 形态。
func ContentDigest(data []byte, algorithm string) (string, error) {
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
