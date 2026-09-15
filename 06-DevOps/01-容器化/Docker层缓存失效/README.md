# Docker 镜像层与构建缓存失效

## 简介

BuildKit 的构建缓存是**内容寻址**的:每条指令产出一个只读层,层的身份由「父层的缓存键 + 本指令自身的摘要」共同决定。命中就跳过执行,不命中就重建,**并且它后面所有指令的缓存键都跟着变**——这就是 `Dockerfile` 行序能决定 CI 快慢的根本原因。

关键概念:

- **缓存键(cache key)**:父层键与指令摘要的哈希;父层键是递归定义的,所以它是一个链而非一张表。
- **元数据校验和**:`COPY` / `ADD` / `RUN --mount=type=bind` 额外要算的摘要,覆盖指令匹配到的每个文件,**但 mtime 不参与**。
- **失效级联(cascade)**:任一层未命中,其后所有指令都重新执行,不可能"跳过中间直接复用后面"。
- **构建密钥(build secret)**:内容不进缓存,但密钥的 ID、挂载路径进缓存。

历史背景:早期 Docker Engine 用 "legacy builder" 逐层比对,只支持 mtime+size;BuildKit(Docker Engine 23.0 起为默认后端)改为内容摘要并支持 `RUN --mount=type=cache` 这类不落层的缓存。多阶段、并发构建、跳过无关阶段也都是 BuildKit 才有的能力。

## 原理详解

1. **从基础镜像开始逐层比对**。官方原文:"The builder begins by checking if the base image is already cached. Each subsequent instruction is compared against the cached layers. If no cached layer matches the instruction exactly, the cache is invalidated."
2. **绝大多数指令只比指令文本**。`ENV` / `LABEL` / `EXPOSE` / `CMD` 之类,文本一样就命中。
3. **只有三类指令需要看文件**:`ADD`、`COPY`、以及带 bind 挂载的 `RUN --mount=type=bind`。此时"the builder calculates a cache checksum from file metadata to determine whether cache is valid"。
4. **mtime 被显式排除**:"The modification time of a file (mtime) is not taken into account when calculating the cache checksum. If only the mtime of the copied files have changed, the cache is not invalidated." → `git checkout` 会重写整棵树的 mtime,但**不会**打穿缓存;而 `chmod` 改了权限位,**会**打穿。
5. **普通 `RUN` 不看文件系统**:"Aside from the ADD and COPY commands, cache checking doesn't look at the files in the container to determine a cache match." 因此 `RUN apt-get -y update` 的匹配只依赖命令字符串,**重建镜像不会让包变新**。
6. **级联**:"Once the cache is invalidated, all subsequent Dockerfile commands generate new images and the cache isn't used."
7. **`WORKDIR` 额外受 `SOURCE_DATE_EPOCH` 影响**:该构建参数变化会让 `WORKDIR` 及其后续全部失效。可复现构建用固定时间戳(`--build-arg SOURCE_DATE_EPOCH=0`)。
8. **构建密钥**:内容不参与缓存;ID 与挂载路径参与。想"轮换密钥时强制失效",要另传一个 `ARG`。

### 缓存键推导示意

```
FROM python:3.12-slim      key0 = H(scratch, "from:python:3.12-slim")
WORKDIR /app               key1 = H(key0, "workdir:/app", SOURCE_DATE_EPOCH)
COPY package.json ./       key2 = H(key1, "copy:package.json", meta_checksum(deps))
RUN pip install -r ...     key3 = H(key2, "run:pip install -r package.json")
COPY . .                   key4 = H(key3, "copy:COPY . .", meta_checksum(ctx))   ← 改源码只从这里开始变
RUN python -m compileall . key5 = H(key4, "run:python -m compileall .")
```

`meta_checksum` 的实现里**没有 mtime 这一项**,这是规则 4 的落点。

### 失效传播示意

```
冷启动         [FROM][WORKDIR][COPY deps][RUN install][COPY .][RUN build]
                 MS   MS       MS          MS           MS       MS
改一行源码      H     H        H           H            MS       MS
                 └── 依赖安装层被保住 ──┘  └─ 只有这两步重跑 ─┘

坏行序(先 COPY . .)
冷启动         [FROM][WORKDIR][COPY . . ][RUN install][RUN build]
改一行源码      H     H        MS          MS           MS
                 └─ 依赖安装被迫重跑,冷启动多等一次全量 pip install ─┘
```

## 对比 / 选型

| 维度 | legacy builder | BuildKit(默认) |
| --- | --- | --- |
| 缓存键 | 指令文本 +(`ADD`/`COPY`)mtime 与 size | 指令文本 + 文件元数据校验和(**不含 mtime**) |
| 无关阶段 | 处理到 `--target` 为止的**全部**阶段 | 只处理 target **依赖**的阶段 |
| 并发 | 否 | 无依赖阶段并发构建 |
| 不落层的缓存 | 不支持 | `RUN --mount=type=cache` |
| 缓存导出 | `docker save` | `--cache-to`(`mode=min` 只导最终镜像层,`mode=max` 连中间步骤层一起导) |

`--mount=type=cache` 与层缓存的区别很关键:层缓存靠"父层没变"复用,**依赖清单一变就全丢**;cache mount 是独立于层的可复用目录,`requirements.txt` 改了 pip 的下载缓存仍在。

## 环境准备

- 操作系统:任意(本 demo 纯逻辑模拟,不调用 Docker)
- 语言:Python 3.10+(用到 `dict | dict` 与 `str | None`)、Go 1.21+
- 依赖:无第三方库

## 运行方式

### Python

```bash
python3 layer_cache.py
```

### Go

```bash
cd go && go run .
```

## 关键代码片段

```python
def digest(self, args, source_date_epoch):
    if self.kind in ("COPY", "ADD"):
        # 规则 3+4:指令文本 + 文件元数据校验和(mtime 被 metadata_checksum 丢弃)
        return sha16("copy", self.text, metadata_checksum(self.files or {}))
    if self.kind == "RUN" and self.secret:
        sid, _content = self.secret   # 规则 8:只要 id,不要内容
        return sha16("run-secret", self.text, sid)
    if self.kind == "RUN":
        return sha16("run", self.text)    # 规则 5:只看命令字符串
    if self.kind == "WORKDIR":
        return sha16("workdir", self.text, source_date_epoch)  # 规则 7
    ...

def build(steps, store, args=None, source_date_epoch="0"):
    parent = sha16("scratch")
    for st in steps:
        key = sha16(parent, st.digest(args or {}, source_date_epoch))
        if store.lookup(key):   # 规则 1+6:父层键进哈希 -> 失效自动级联
            res.hits += 1
        else:
            res.misses += 1
        parent = key            # 关键:把本步的键交给下一步当"父层"
```

## 性能与边界

- 缓存收益量级取决于"被保住的最贵步骤":本 demo 里改一行源码,好行序重跑 **1** 个 `RUN`,坏行序重跑 **2** 个——两者差的就是一次依赖安装(真实项目里 `pip install` / `npm ci` 常是构建耗时的大头)。
- 级联是**按步数**放大的:越靠前的层越"贵",因为失效后要重建的步数越多。故应把变更频率最低的指令排在最前。
- 缓存键是链式的 → 层的数量与 `Dockerfile` 长度线性相关,但**缓存命中时不会下载层**。
- 不能跳步复用是硬约束:即使后面某条指令的文本与历史完全一致,只要父层键变了,它的键也变了。
- 磁盘上缓存会持续增长,需要 `docker builder prune` 回收;`--no-cache-filter <stage>` 可只让某个阶段失效。

## 注意事项与常见坑

1. **官方文档前后版本措辞不一致**(重要):旧版 invalidation 页写 "the modification time **and size** file metadata is used to determine whether cache is valid";现行页面改为"cache checksum from file metadata"并明确 **mtime 不参与**。本 demo 以**现行官方页面**为准,并在模型里显式保留 mtime 字段却不用它,以便断言这条规则。
2. **`COPY . .` 放太前**是最常见写法错误:任何源码改动都会连带重装依赖。正确做法是先 `COPY` 依赖清单 → 装依赖 → 再 `COPY . .`。
3. **`RUN apt-get update` 不会自动变新**:命令字符串没变就永远命中,重建一周后拿到的还是旧包。要强制重建:改动其前面的层 / `docker builder prune` / `--no-cache` / `--no-cache-filter`。
4. **`git checkout` 不会打穿缓存,但 `chmod` 会**:mtime 被排除,权限位没有。CI 里做 `chmod +x` 这类操作要放在依赖安装之后。
5. **`.dockerignore` 直接决定 `COPY` 的输入**:构建上下文里混进 `node_modules/`、`target/`、日志,既拖慢上传又容易意外失效。
6. **构建密钥不能当缓存开关**:轮换 token 不会失效,必须配合 `ARG CACHEBUST`。
7. **`--cache-to=...mode=max` 会把构建阶段的层(源码树、私有依赖)一起导出**:多阶段构建"构建阶段不进最终镜像"说的是**不被 manifest 引用**,不等于缓存里没有。共享缓存前要评估这一点。
8. **`--from` 里不能用构建参数**:`COPY --from=build-${src}` 非法,因为阶段依赖必须在构建开始前确定;解决办法是先 `FROM build-${src} AS alias` 再 `COPY --from=alias`。

## 参考资料(实际阅读过的权威来源)

- [Docker — Build cache invalidation](https://docs.docker.com/build/cache/invalidation/) — 逐指令缓存规则原文:mtime 不参与校验和、"just the command string itself is used"、`SOURCE_DATE_EPOCH` 与 `WORKDIR`、构建密钥内容不参与缓存
- [Docker docs — build: add cache invalidation page(commit 699739c)](https://github.com/docker/docs/commit/699739c2797ac917c65769f02893ddee8bf2e705) — 新旧措辞差异的原始 diff(旧版 "modification time and size" → 新版 "cache checksum from file metadata")
- [Docker — Build cache / garbage collection 文档入口](https://docs.docker.com/build/cache/) — 层与缓存的关系、`docker builder prune` 与 `--cache-to` 的 min/max 语义
- [Docker blog — Advanced Dockerfiles: Faster Builds and Smaller Images Using BuildKit and Multistage Builds](https://www.docker.com/?p=25914) — BuildKit 跳过无关阶段、并发构建、`--from` 不可含构建参数及其原因
