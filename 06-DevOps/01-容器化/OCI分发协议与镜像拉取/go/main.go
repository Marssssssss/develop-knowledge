// OCI Distribution Spec —— Go 侧演示入口（与 python/main.py 同题）。
package main

import "fmt"

func pushBlob(rg *Registry, name string, data []byte) (string, int) {
	r, _ := rg.PostUpload(name, "", "")
	uid := r.Headers["Docker-Upload-UUID"]
	dgst := digestOf(data)
	r2, err := rg.PutUpload(uid, dgst, data, "")
	if err != nil {
		return "", err.Status
	}
	return dgst, r2.Status
}

func main() {
	rg := NewRegistry(true)
	rg.EnsureRepo("app")

	fmt.Println("== 1. 探测与整体上传 ==")
	fmt.Println("   GET /v2/ ->", rg.GetV2().Status)
	dgst, st := pushBlob(rg, "app", []byte("layer-bytes"))
	got, _ := rg.GetBlob("app", dgst)
	fmt.Printf("   blob %s.. PUT -> %d, GET 内容=%q\n", dgst[:16], st, got.Body)

	fmt.Println("\n== 2. 分块上传 ==")
	r, _ := rg.PostUpload("app", "", "")
	uid := r.Headers["Docker-Upload-UUID"]
	offset := 0
	for _, c := range []string{"AAAAA", "BBBBB", "C"} {
		rng := fmt.Sprintf("%d-%d", offset, offset+len(c)-1)
		pr, err := rg.PatchUpload(uid, []byte(c), rng)
		if err != nil {
			fmt.Println("   PATCH", rng, "->", err.Status, err.Code)
			break
		}
		fmt.Printf("   PATCH %-8s -> %d Range %s\n", rng, pr.Status, pr.Headers["Range"])
		offset += len(c)
	}
	pr, err := rg.PutUpload(uid, digestOf([]byte("AAAAABBBBBC")), nil, "")
	if err != nil {
		fmt.Println("   PUT ->", err.Status, err.Code)
	} else {
		fmt.Println("   PUT 关闭会话 ->", pr.Status)
	}

	fmt.Println("\n== 3. 乱序 chunk 被 416 拒 ==")
	r2, _ := rg.PostUpload("app", "", "")
	if _, err := rg.PatchUpload(r2.Headers["Docker-Upload-UUID"], []byte("X"), "5-9"); err != nil {
		fmt.Println("   PATCH 5-9 ->", err.Status, err.Code)
	}

	fmt.Println("\n== 4. 标签分页（last 非包含）==")
	repo := rg.EnsureRepo("app")
	repo.Tags["v10"], repo.Tags["V2"], repo.Tags["v1"] = dgst, dgst, dgst
	all, _ := rg.ListTags("app", -1, "")
	fmt.Println("   全部:", all.Body)
	page, _ := rg.ListTags("app", 2, "")
	fmt.Printf("   n=2: %v Link=%v\n", page.Body, page.Headers["Link"])
	after, _ := rg.ListTags("app", 1, "V2")
	fmt.Println("   n=1 last=V2:", after.Body)
	zero, _ := rg.ListTags("app", 0, "")
	fmt.Printf("   n=0: %v Link=%q\n", zero.Body, zero.Headers["Link"])

	fmt.Println("\n== 5. 跨仓库挂载与删除 ==")
	rg.EnsureRepo("base").Blobs[dgst] = []byte("layer-bytes")
	mr, _ := rg.PostUpload("app", dgst, "base")
	fmt.Println("   mount ->", mr.Status)
	if dr, err := rg.DeleteBlob("app", dgst); err != nil {
		fmt.Println("   DELETE blob ->", err.Status, err.Code)
	} else {
		fmt.Println("   DELETE blob ->", dr.Status)
	}
	if _, err := rg.DeleteBlob("app", dgst); err != nil {
		fmt.Println("   再删一次 ->", err.Status, err.Code)
	}
}
