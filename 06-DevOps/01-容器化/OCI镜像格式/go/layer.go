// OCI layer 序列化与应用 —— opencontainers/image-spec/layer.md + config.md 的
// 标识符算法(DiffID / ChainID)与 whiteout 应用语义,供 oci_image.go 使用。
package main

import (
	"archive/tar"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"io"
	"os"
	"path/filepath"
	"sort"
	"time"
)

func time0() time.Time { return time.Unix(0, 0) } // 确定性时间戳

func digest(b []byte) string { // descriptor 用的 "sha256:<hex>" 格式
	h := sha256.Sum256(b)
	return "sha256:" + hex.EncodeToString(h[:])
}

// buildLayer 把 map[path]content 序列化为确定性 layer tar(未压缩)。
// 确定性:路径排序、mtime=0、uid/gid=0、无 uname/gname —— 同一内容永远同一 DiffID。
func buildLayer(entries map[string][]byte) ([]byte, error) {
	paths := make([]string, 0, len(entries))
	for p := range entries {
		paths = append(paths, p)
	}
	sort.Strings(paths)

	var buf bytes.Buffer
	tw := tar.NewWriter(&buf)
	for _, p := range paths {
		content := entries[p]
		hdr := &tar.Header{
			Name: p, Mode: 0o644, Size: int64(len(content)),
			ModTime: time0(), Uname: "", Gname: "",
		}
		if err := tw.WriteHeader(hdr); err != nil {
			return nil, err
		}
		if _, err := tw.Write(content); err != nil {
			return nil, err
		}
	}
	if err := tw.Close(); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

// chainIDs: ChainID(L0)=DiffID(L0);ChainID(前缀|Ln)=Digest(前缀+" "+DiffID(Ln))。
// 注意分隔符是一个空格且参与哈希(image-spec/config.md)。
func chainIDs(diffIDs []string) []string {
	chain := []string{diffIDs[0]}
	for _, d := range diffIDs[1:] {
		chain = append(chain, digest([]byte(chain[len(chain)-1]+" "+d)))
	}
	return chain
}

// applyLayer 按 layer.md 的"应用"语义解包(而非普通 tar 解压):
// 1) 先应用 whiteout(.wh.<name> 删父层同名条目;.wh..wh..opq 清空父层目录)
// 2) 再写普通条目;目标已存在时先删再建("semantic equivalent of unlink+recreate")
// 3) whiteout 标记本身不出现在最终文件系统里
func applyLayer(root string, tarBytes []byte) error {
	type entry struct {
		name    string
		content []byte
	}
	var whiteouts, entries []entry

	tr := tar.NewReader(bytes.NewReader(tarBytes))
	for {
		hdr, err := tr.Next()
		if err != nil { // io.EOF 结束
			break
		}
		content, err := io.ReadAll(tr) // 读取当前条目(最多 hdr.Size 字节)
		if err != nil {
			return err
		}
		name := filepath.ToSlash(filepath.Clean(hdr.Name))
		if base := filepath.Base(name); len(base) > 4 && base[:4] == ".wh." {
			whiteouts = append(whiteouts, entry{name, content})
		} else {
			entries = append(entries, entry{name, content})
		}
	}

	for _, w := range whiteouts { // 1) whiteout 先应用
		base := filepath.Base(w.name)
		target := filepath.Join(root, filepath.Dir(w.name), base[4:])
		if base == ".wh..wh..opq" { // opaque:清空同目录父层子项
			parent := filepath.Dir(target)
			items, _ := os.ReadDir(parent)
			for _, c := range items {
				_ = os.RemoveAll(filepath.Join(parent, c.Name()))
			}
		} else { // 显式:删除父层同名条目
			_ = os.RemoveAll(target)
		}
	}
	for _, e := range entries { // 2) 覆盖写(删旧建新)
		path := filepath.Join(root, e.name)
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			return err
		}
		_ = os.Remove(path)
		if err := os.WriteFile(path, e.content, 0o644); err != nil {
			return err
		}
	}
	return nil
}
