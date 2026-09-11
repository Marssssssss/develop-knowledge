// OCI 镜像格式最小实现 —— opencontainers/image-spec 实战(Go 版).
//
// 端到端流程与 Python 版一致:
//  1. 构造 3 个 layer(纯 tar,含 whiteout)          [image-spec/layer.md]
//  2. DiffID = sha256(未压缩 tar)                    [image-spec/config.md]
//  3. config JSON(rootfs.diff_ids 自底向上)
//  4. gzip 压缩,blob digest = sha256(压缩后内容)
//  5. manifest(config + layers descriptor,schemaVersion=2)
//  6. 写 OCI image-layout(blobs/ + index.json + oci-layout)
//  7. 验证:重读,校验 digest,算 ChainID,按层序解包,比对文件树
//
// layer 的序列化/应用算法见同目录 layer.go。
// 运行: go run .(输出 out_image/ 与 out_rootfs/,任何平台可跑)
package main

import (
	"bytes"
	"compress/gzip"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
)

const (
	mediaManifest = "application/vnd.oci.image.manifest.v1+json"
	mediaConfig   = "application/vnd.oci.image.config.v1+json"
	mediaLayerGZ  = "application/vnd.oci.image.layer.v1.tar+gzip"
	mediaIndex    = "application/vnd.oci.image.index.v1+json"
)

// 三层设计:base(普通文件) / app(新增+显式 whiteout) / cleanup(opaque)。
var layer1 = map[string][]byte{
	"etc/hostname": []byte("demo-box\n"),
	"etc/motd":     []byte("welcome to base layer\n"),
	"bin/sh":       []byte("#!/bin/sh\n# busybox-ish shell placeholder\n"),
	"tmp/a":        []byte("temp file a\n"),
	"tmp/b":        []byte("temp file b\n"),
}
var layer2 = map[string][]byte{
	"bin/tool":     []byte("#!/bin/sh\n# app tool, modifies nothing\n"),
	"etc/.wh.motd": {}, // .wh.<name> = 应用本层时删除父层 <name>
	"root/.keep":   {},
}
var layer3 = map[string][]byte{
	"tmp/.wh..wh..opq": {}, // opaque:父层同目录所有子项不可见
}

var expected = map[string][]byte{ // 逐层应用 whiteout 语义后的期望文件树
	"etc/hostname": []byte("demo-box\n"),
	"bin/sh":       []byte("#!/bin/sh\n# busybox-ish shell placeholder\n"),
	"bin/tool":     []byte("#!/bin/sh\n# app tool, modifies nothing\n"),
	"root/.keep":   {},
}

// Descriptor: image-spec 的内容寻址引用(mediaType 由调用方补)。
type Descriptor struct {
	MediaType string `json:"mediaType,omitempty"`
	Digest    string `json:"digest"`
	Size      int64  `json:"size"`
}

func compactJSON(v interface{}) []byte { // 确定性 JSON(无多余空白)
	b, _ := json.Marshal(v)
	return b
}

func buildImage(layoutDir string) (imageID string, err error) {
	if err = os.MkdirAll(filepath.Join(layoutDir, "blobs/sha256"), 0o755); err != nil {
		return
	}
	putBlob := func(content []byte) (Descriptor, error) {
		d := digest(content)
		path := filepath.Join(layoutDir, "blobs/sha256", d[7:])
		return Descriptor{Digest: d, Size: int64(len(content))},
			os.WriteFile(path, content, 0o644)
	}

	layers := []map[string][]byte{layer1, layer2, layer3}
	var diffIDs []string
	var layerDescs []Descriptor
	for _, l := range layers {
		var tarBytes []byte
		if tarBytes, err = buildLayer(l); err != nil {
			return
		}
		diffIDs = append(diffIDs, digest(tarBytes)) // DiffID:未压缩
		var gzBuf bytes.Buffer                      // mtime=0 保证可复现
		zw := gzip.NewWriter(&gzBuf)
		zw.ModTime = time0()
		if _, err = zw.Write(tarBytes); err != nil {
			return
		}
		if err = zw.Close(); err != nil {
			return
		}
		var d Descriptor
		if d, err = putBlob(gzBuf.Bytes()); err != nil {
			return
		}
		d.MediaType = mediaLayerGZ
		layerDescs = append(layerDescs, d)
	}

	configBytes := compactJSON(map[string]interface{}{
		"architecture": "amd64", "os": "linux",
		"rootfs": map[string]interface{}{"type": "layers", "diff_ids": diffIDs},
		"history": []string{"base", "app", "cleanup"},
	})
	var configDesc Descriptor
	if configDesc, err = putBlob(configBytes); err != nil {
		return
	}
	configDesc.MediaType = mediaConfig

	manifestBytes := compactJSON(map[string]interface{}{
		"schemaVersion": 2, "mediaType": mediaManifest,
		"config": configDesc, "layers": layerDescs,
	})
	var manifestDesc Descriptor
	if manifestDesc, err = putBlob(manifestBytes); err != nil {
		return
	}
	manifestDesc.MediaType = mediaManifest

	index := map[string]interface{}{
		"schemaVersion": 2, "mediaType": mediaIndex,
		"manifests": []map[string]interface{}{{
			"mediaType":   manifestDesc.MediaType,
			"digest":      manifestDesc.Digest,
			"size":        manifestDesc.Size,
			"annotations": map[string]string{"org.opencontainers.image.ref.name": "demo:latest"},
		}},
	}
	if err = os.WriteFile(filepath.Join(layoutDir, "index.json"),
		compactJSON(index), 0o644); err != nil {
		return
	}
	if err = os.WriteFile(filepath.Join(layoutDir, "oci-layout"),
		[]byte(`{"imageLayoutVersion":"1.0.0"}`), 0o644); err != nil {
		return
	}
	return digest(configBytes), nil // ImageID = SHA256(config JSON)
}

func verifyImage(layoutDir, rootDir string) error {
	indexBytes, err := os.ReadFile(filepath.Join(layoutDir, "index.json"))
	if err != nil {
		return err
	}
	var index struct {
		Manifests []Descriptor `json:"manifests"`
	}
	if err = json.Unmarshal(indexBytes, &index); err != nil {
		return err
	}
	blob := func(d string) ([]byte, error) {
		b, err := os.ReadFile(filepath.Join(layoutDir, "blobs/sha256", d[7:]))
		if err != nil {
			return nil, err
		}
		if digest(b) != d {
			return nil, fmt.Errorf("digest 校验失败: %s", d)
		}
		return b, nil
	}
	manifestBytes, err := blob(index.Manifests[0].Digest)
	if err != nil {
		return err
	}
	var manifest struct {
		Config Descriptor   `json:"config"`
		Layers []Descriptor `json:"layers"`
	}
	if err = json.Unmarshal(manifestBytes, &manifest); err != nil {
		return err
	}
	var config struct {
		Rootfs struct {
			DiffIDs []string `json:"diff_ids"`
		} `json:"rootfs"`
	}
	if configBytes, _ := blob(manifest.Config.Digest); configBytes != nil {
		if err = json.Unmarshal(configBytes, &config); err != nil {
			return err
		}
	}

	var diffIDs []string
	for _, desc := range manifest.Layers { // 逐层解压校验并应用
		gz, err := blob(desc.Digest)
		if err != nil {
			return err
		}
		zr, err := gzip.NewReader(bytes.NewReader(gz))
		if err != nil {
			return err
		}
		var tarBuf bytes.Buffer
		if _, err = tarBuf.ReadFrom(zr); err != nil {
			return err
		}
		diffIDs = append(diffIDs, digest(tarBuf.Bytes()))
		if err = applyLayer(rootDir, tarBuf.Bytes()); err != nil {
			return err
		}
	}
	chain := chainIDs(diffIDs)
	for i := range diffIDs { // ChainID 递归校验(打印)
		fmt.Printf("  DiffID %.16s.. -> ChainID %.16s..\n", diffIDs[i], chain[i])
	}
	if fmt.Sprint(diffIDs) != fmt.Sprint(config.Rootfs.DiffIDs) {
		return fmt.Errorf("DiffID 与 config.rootfs.diff_ids 不一致")
	}

	actual := map[string][]byte{} // 比对最终文件树
	walkErr := filepath.Walk(rootDir, func(path string, info os.FileInfo, _ error) error {
		if !info.IsDir() {
			rel, _ := filepath.Rel(rootDir, path)
			b, _ := os.ReadFile(path)
			actual[filepath.ToSlash(rel)] = b
		}
		return nil
	})
	if walkErr != nil {
		return walkErr
	}
	if fmt.Sprint(actual) != fmt.Sprint(expected) {
		return fmt.Errorf("文件树不符: %v", actual)
	}
	fmt.Println("  最终文件树与期望一致(whiteout / opaque 语义正确)")
	return nil
}

func main() {
	const outDir, rootDir = "out_image", "out_rootfs"
	_ = os.RemoveAll(outDir)
	_ = os.RemoveAll(rootDir)
	if err := os.MkdirAll(rootDir, 0o755); err != nil {
		panic(err)
	}
	imageID, err := buildImage(outDir)
	if err != nil {
		panic(err)
	}
	fmt.Printf("ImageID(= SHA256(config JSON)) : %s\n", imageID)
	fmt.Println("== 验证: 重读 image-layout 并解包 ==")
	if err := verifyImage(outDir, rootDir); err != nil {
		panic(err)
	}
	fmt.Println("\nOCI image-layout 写出完成:", outDir)
}
