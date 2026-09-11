# OCI 镜像格式(image-spec)

> 容器三大基石之三:namespaces 管"看不见"、cgroups 管"用不多",**镜像格式管"怎么存、怎么传"**。Docker image v2 与 OCI image-spec 同源(schemaVersion=2 即为兼容保留),本 demo 从零构造一个符合规范的 OCI 镜像并按规范语义解包验证。

## 简介

- OCI Image 是**有序的根文件系统变更集(changeset)集合 + 对应的执行参数**(config)的打包格式(image-spec/config.md:"An OCI Image is an ordered collection of root filesystem changes and the corresponding execution parameters")
- 三个目标(manifest.md 开篇):**内容寻址**(config 哈希即镜像 ID)、**多架构**(image index 引用各平台 manifest)、**可翻译到 runtime-spec**
- 关键概念:
  - **Layer**:tar 归档形式的文件系统变更(增/改/删),不含环境变量等镜像级配置
  - **Whiteout**:`.wh.<name>` 空文件标记"应用本层时删除父层的 `<name>`"
  - **Descriptor**:`{mediaType, digest, size}` 三元组,一切内容(blob)靠 sha256 digest 寻址
  - **Image-layout**:磁盘布局 `blobs/sha256/<hex>` + `index.json` + `oci-layout`

## 原理详解

### 四类标识符(最易混淆,全部出自 config.md)

| 标识符 | 定义 | 用在哪 |
| --- | --- | --- |
| **DiffID** | `sha256(未压缩 layer tar)` | `config.rootfs.diff_ids` |
| **blob digest** | `sha256(压缩后 layer 内容)` | `manifest.layers[].digest` |
| **ChainID** | 递归复合哈希(见下) | 标识"层栈应用结果",OCI layout 存储用 |
| **ImageID** | `sha256(config JSON)` | 镜像唯一 ID,内容寻址 |

```text
ChainID(L0)            = DiffID(L0)
ChainID(L0|...|Ln)     = Digest( ChainID(L0|...|Ln-1) + " " + DiffID(Ln) )
ImageID                = SHA256(config JSON)
```

ChainID 存在的理由(config.md 原文解释):单独的 DiffID(C) 无法唯一确定 A|B|C 的应用结果 —— 若只认 C,攻击者可构造任意前缀 x 使 `x|C == C` 成立;复合哈希把"应用顺序"编进了标识符。

### manifest 结构(manifest.md)

```json
{
  "schemaVersion": 2,
  "mediaType": "application/vnd.oci.image.manifest.v1+json",
  "config":  {"mediaType": "application/vnd.oci.image.config.v1+json",
              "digest": "sha256:...", "size": 7023},
  "layers": [ {"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
               "digest": "sha256:...", "size": 32654}, ... ]
}
```

- `schemaVersion` **必须为 2**(向后兼容 Docker);`layers[0]` 必须是 base 层,其后按栈序向上
- 层媒体类型:`...layer.v1.tar`(未压缩)/ `+gzip` / `+zstd`

### layer 应用语义(layer.md,注意不是普通解压)

1. **先应用 whiteout,再写普通条目**
2. 显式 whiteout:`.wh.motd` → 删除父层的 `motd`;whiteout 只作用于**父层**,同层文件只能被后续层的 whiteout 隐藏
3. 不透明 whiteout:`<dir>/.wh..wh..opq` → 父层该目录**全部子项**不可见(目录本身保留)
4. 目标已存在时:两个都是目录则替换属性;否则**语义上等价于 unlink + 重建**(不是原地修改)
5. 白出标记本身应用后**必须隐藏**(不能在最终文件系统里看到 `.wh.` 文件)

### 磁盘布局(image-layout)

```text
out_image/
├── oci-layout                 # {"imageLayoutVersion":"1.0.0"}
├── index.json                 # 入口:引用 manifest descriptor + ref name 注解
└── blobs/sha256/<hex>         # 所有内容寻址 blob:layer / config / manifest 混存
```

## 对比 / 选型

| 方案 | 特点 |
| --- | --- |
| Docker image manifest v2 schema 2 | OCI 的前身,OCI 兼容之(反向亦然) |
| OCI image-spec 1.0(2017)/ 1.1(2024) | 事实标准;containerd / CRI-O / Docker / Podman 均实现 |
| artifact 用法(1.1) | `artifactType` + 空 config descriptor,非容器内容(Helm chart 等)也走此管道 |

## 环境准备

- 任何 OS;Python 3.8+(仅标准库)或 Go 1.21+(仅标准库)

## 运行方式

```bash
cd python && python3 oci_image.py        # 构建镜像 -> 校验 -> 解包比对
cd go     && go run .                    # 同上,输出 out_image/ 与 out_rootfs/
```

## 关键代码片段(Python 版 ChainID 与 whiteout 应用)

```python
def chain_ids(diff_ids):
    chain = [diff_ids[0]]
    for d in diff_ids[1:]:
        chain.append(sha256_digest((chain[-1] + " " + d).encode()))  # 注意空格分隔
    return chain

# apply_layer: 先 whiteout 后普通条目
for w in whiteouts:
    base = os.path.basename(w)
    if base == ".wh..wh..opq":            # opaque: 清空同目录父层子项
        for c in os.listdir(parent): ...
    else:                                 # 显式: 删除父层同名条目
        os.remove(target)
```

## 性能与边界

- 内容寻址天然去重:两个镜像共享 base 层时,registry 与本地存储只存一份 blob —— 这是"层缓存"的机制基础
- 未压缩 tar 与 gzip 后内容是**两个不同的 digest**(DiffID ≠ blob digest),混淆二者是新手头号错误
- 可复现性要求 tar 头确定(mtime/uid/gid/顺序);规范建议 tar-split 保存原始 tar 头,demo 用全零时间戳达成
- 镜像层数上限:overlayfs 默认支持 ~128 层,旧 aufs 127 层

## 注意事项与常见坑

1. **DiffID 是未压缩 tar 的哈希,blob digest 是压缩后内容的哈希** —— manifest 里写后者,config.rootfs.diff_ids 里写前者,两套不能混用(config.md 明确警告 "Do not confuse DiffIDs with layer digests")
2. **ChainID 递归的分隔符是一个空格** `" "` 且参与哈希 —— 忘了空格或用换行都会得到错误 ChainID
3. **whiteout 只删父层内容**:想在同层"删除"做不到;本 demo 的 L2 whiteout 删的是 L1 的 motd
4. **`.wh.` 开头的文件名在真实文件系统里创建不出来**(overlayfs 上),所以 whiteout 只存在于 tar 归档内 —— 应用层时必须显式处理并隐藏它们
5. **opaque whiteout 后目录本身还在**,只是父层子项全不可见 —— 期望文件树比对时要包含该目录
6. **gzip 输出必须固定 mtime=0** 才可复现;否则同一 tar 每次压缩出不同 digest,"缓存命中"永远失效
7. config JSON 的**序列化字节本身参与 ImageID 哈希** —— 缩进/键序变化即产生新 ImageID;实现里要用紧凑确定性序列化

## 参考资料(实际阅读过的权威来源)

- [OCI Image Configuration — specs.opencontainers.org](https://specs.opencontainers.org/image-spec/config/) — DiffID / ChainID 递归定义与安全动机、ImageID=SHA256(config)、rootfs.diff_ids 栈序(全文阅读)
- [OCI Image Manifest — specs.opencontainers.org](https://specs.opencontainers.org/image-spec/manifest/) — manifest 字段、schemaVersion=2 兼容性、layers[0]=base 栈序约束、空 descriptor 指南(全文阅读)
- [OCI Image Layer Filesystem Changeset — specs.opencontainers.org](https://specs.opencontainers.org/image-spec/layer/) — whiteout / opaque whiteout 语义、层应用规则(unlink+重建)、媒体类型(全文阅读)
