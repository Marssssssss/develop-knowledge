# OCI 分发协议：镜像是怎么被推上去、拉下来的

> `image-spec` 定义镜像**长什么样**（config / layers / manifest / index），`distribution-spec` 定义镜像**怎么在 registry 与客户端之间搬运**（端点、状态码、分块上传、digest 校验）。本文是后者：把规范里的 14 个端点与 14 个错误码做成一个可跑的 registry 模型。

权威来源（本 demo 实际读过）：

- `opencontainers/distribution-spec` **spec.md**（54228 B，main 分支原文）：§Endpoints（end-1 ~ end-14）、§Error Codes（code-1 ~ code-14）、§Pushing blobs monolithically、§Pushing a blob in chunks、§Mounting a blob from another repository、§Listing Tags、§Listing Referrers、§Deleting

```bash
python python/main.py              # 演示入口：完整 push / pull / list
python python/selfcheck_dist.py    # 66 项断言
go run go/dist_registry.go go/main.go
```

## 1. 端点总表（规范 §Endpoints 原样收录）

| ID | Method | Endpoint | 成功 | 失败 |
| --- | --- | --- | --- | --- |
| end-1 | GET | `/v2/` | 200 | 404 / 401 |
| end-2 | GET/HEAD | `/v2/<name>/blobs/<digest>` | 200 | 404 |
| end-3 | GET/HEAD | `/v2/<name>/manifests/<tag-or-digest>` | 200 | 404 |
| end-4a | POST | `/v2/<name>/blobs/uploads/` | 202 | 404 |
| end-4b | POST | `/v2/<name>/blobs/uploads/?digest=<digest>` | 201/202 | 404/400 |
| end-5 | PATCH | `<blob-push-location>` | 202 | 404 / **416** |
| end-6 | PUT | `<blob-push-location>?digest=<digest>` | 201 | 404/400/416 |
| end-7a | PUT | `/v2/<name>/manifests/<tag-or-digest>` | 201 | 404/413 |
| end-7b | PUT | `.../manifests/<digest>?tag=1&tag=2` | 201 | 404/413 |
| end-8a/b | GET | `/v2/<name>/tags/list[?n=&last=]` | 200 | 404 |
| end-9 | DELETE | `/v2/<name>/manifests/<tag-or-digest>` | 202 | 404/400/**405** |
| end-10 | DELETE | `/v2/<name>/blobs/<digest>` | 202 | 404/400/405 |
| end-11 | POST | `.../blobs/uploads/?mount=<digest>&from=<name>` | 201/202 | 404 |
| end-12 | GET | `/v2/<name>/referrers/<digest>[?artifactType=]` | 200 | 404/400 |
| end-13 | GET | `<blob-push-location>` | **204** | 404 |
| end-14 | DELETE | `<blob-push-location>` | 204 | 404/400 |

注意几个反直觉的状态码：**412 不存在**，分块出错统一是 **416**；删除不支持时是 **405**（`UNSUPPORTED`），不是 501；查询上传进度是 **204 + `Range` 头**，不是 200。

## 2. 整体上传的两条路

**POST 再 PUT**（end-4a + end-6）：

```
POST /v2/app/blobs/uploads/            → 202  Location: <push-loc>  Docker-Upload-UUID: <uuid>
PUT  <push-loc>?digest=sha256:<d>      → 201  Location: /v2/app/blobs/sha256:<d>
```

**单次 POST**（end-4b）：把 `?digest=` 提前给在 POST 上，registry 可以直接落库回 201，也可以仍然回 202 让你走 PUT——规范两种都允许，所以客户端**不能**只看状态码判断有没有成功。

关闭会话时 `PUT` 带的是**整个 blob** 的 digest，不是最后一块的。算错就 400 `DIGEST_INVALID`，错误体 `detail` 里同时给出 provided 与 computed。

## 3. 分块上传：三条 416 判据

三阶段：`POST`（**必须带 `Content-Length: 0`**）→ `PATCH` 若干 → `PUT` 收尾。

`PATCH` 的请求头是 `Content-Range: <start>-<end>`，**两端都包含**，且必须匹配 `^[0-9]+-[0-9]+$`。响应回 `202` + `Range: 0-<end-of-range>`（累计值，不是本次的范围）。

三条判据（规范原文 + 本 demo 的断言）：

1. **首个 chunk 必须从 0 开始** → 否则 416；
2. **chunks 必须按顺序**，下一个 chunk 的首字节 = 上一个 `<end-of-range> + 1` → 错位 416；
3. `Content-Length` 与 range 长度不符 → 416。

收到 416 后，用 `GET <blob-push-location>`（end-13）拿当前合法偏移与新的上传位置，然后续传。`OCI-Chunk-Min-Length` 是 registry 在 POST 响应里给的**建议最小块**（最后一块可以小于它）。

最后一个 chunk 既可以用 `PATCH` 传，也可以塞在收尾的 `PUT` 里，但**无论如何都要有一次 `PUT`**。

## 4. 跨仓库挂载（end-11）

同一个 registry 内、另一个仓库已有的 blob，可以直接挂载而不重传：

```
POST /v2/app/blobs/uploads/?mount=sha256:<d>&from=base
```

- `base` 里有这个 blob → **201**，目标仓库立刻可拉；
- 没有 → **202**，语义退化为「普通上传会话」，客户端老老实实重传。

这正是「为什么推第二个镜像快得多」：层已经以 digest 形式存在 registry 上，挂载只是加一条引用。

## 5. manifest：引用完整性由 registry 兜底

`PUT manifest` 时 registry 会检查它引用的 config 与每一层是否都已存在，缺失就 **404 `MANIFEST_BLOB_UNKNOWN`**，并在 `detail.digests` 里列出缺哪些。**必须先推完所有 blob 再推 manifest**——顺序反了会被直接拒。

`end-7b` 允许一次 PUT 挂多个 tag：`/v2/app/manifests/<digest>?tag=latest&tag=stable`。

拉取时 `Docker-Content-Digest` 头返回实际 digest；规范明确说它 **MAY differ from the provided digest**（比如 registry 做了格式规范化），所以客户端要用响应头里的值，而不是自己算的。

## 6. 标签分页：`last` 是非包含的

- 顺序：lexical（大小写不敏感）或 **ASCIIbetical**（Go `sort.Strings`，字节序）——本 demo 用后者，所以 `V1` 排在 `v10` 前面；
- `?n=<int>` 取前 n 个，还有余量时回 `Link: <...>; rel="next"`；
- `?last=<tag>` 返回该 tag **之后**的 n 个，**不含 last 本身**；
- **`n=0` 必须返回空列表且不带 `Link` 头**；
- 实现者提示：老版本规范没有 `Link`，有 `Link` 时应优先用它翻页，别拿「返回个数 < n」当终止条件。

## 7. 错误体与 14 个错误码

4XX 的响应体**可以**是任意格式，但如果是 JSON，就必须是：

```json
{"errors": [{"code": "<CODE>", "message": "...", "detail": {...}}]}
```

`code` 只允许大写字母与下划线，取值固定 14 个：`BLOB_UNKNOWN`、`BLOB_UPLOAD_INVALID`、`BLOB_UPLOAD_UNKNOWN`、`DIGEST_INVALID`、`MANIFEST_BLOB_UNKNOWN`、`MANIFEST_INVALID`、`MANIFEST_UNKNOWN`、`NAME_INVALID`、`NAME_UNKNOWN`、`SIZE_INVALID`、`UNAUTHORIZED`、`DENIED`、`UNSUPPORTED`、`TOOMANYREQUESTS`。429 还应带 `Retry-After`。

## 8. Referrers（1.1 新增）：把 SBOM / 签名挂到镜像上

`GET /v2/<name>/referrers/<digest>` 返回**一个 image index**，里面每个 descriptor 都带 `artifactType`。要点：

- 支持该 API 的 registry **不得**对 referrers 请求返回 404；不支持时客户端回落到 referrers tag schema；
- digest 语法非法 → **400**；
- 查无结果 → **200 + 空 manifests 列表**，不是 404；
- `artifactType` 缺失时，取 config descriptor 的 `mediaType` 兜底；
- 过滤生效时必须回 `OCI-Filters-Applied: artifactType`。

## 9. 与既有 demo 的分工

- `OCI镜像格式`：DiffID / ChainID / ImageID、whiteout（**镜像结构**）；
- 本 demo：端点、分块上传、digest 校验、标签分页（**分发协议**）；
- `CNI插件模型与IPAM`：容器网络控制面，与镜像分发是两条独立链路。

## 10. 自检覆盖

66 项断言，分八组：仓库命名与 `/v2/` 探测、整体上传与 `DIGEST_INVALID`、分块上传（累计 Range、三条 416、进度查询、取消后 `BLOB_UPLOAD_UNKNOWN`）、跨仓库挂载（命中 201 / 未命中 202）、manifest（引用缺失、按 tag 与 digest 拉取、多 tag PUT）、标签分页（排序、`Link`、`last` 非包含、`n=0`）、删除与错误体格式、referrers（过滤头、空列表、400、回落 404）。
