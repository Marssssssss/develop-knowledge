# 多阶段构建(multi-stage builds)与镜像瘦身

## 简介

多阶段构建指的是在**一个 Dockerfile 里写多个 `FROM`**。每个 `FROM` 开启一个独立的构建阶段,阶段之间可以用 `COPY --from=<stage|镜像>` 搬运文件,而**只有 target 阶段会被打成最终镜像**。

关键概念:

- **stage(阶段)**:`FROM` 之后到下一个 `FROM` 之间的指令集合;可以用 `AS <name>` 命名,否则用从 0 开始的序号。
- **target 阶段**:`docker build` 默认取最后一个阶段;`--target <name>` 可指定停在某个阶段。
- **产物搬运**:`COPY --from=build /out/app /app` —— 只搬这一个文件,构建阶段里的工具链、源码树、中间产物都不跟过来。
- **阶段依赖图**:`FROM <stage>` 与 `COPY --from=<stage>` 是同一类引用,决定了哪些阶段必须被构建。
- **BuildKit 的剪枝**:只构建 target **依赖**的阶段;legacy builder 会把 target 之前的阶段全部跑一遍。

历史背景:在只有单阶段 Dockerfile 的年代,要瘦身就得写两个 Dockerfile + 一个 shell 脚本(先 build、再 `docker cp` 产物、再 build 运行镜像)。多阶段把这个流程收进一个文件,不再需要中间镜像和本地导出。

## 原理详解

1. **每个 `FROM` 开一个新阶段**,基础镜像可以各不相同。
2. **`COPY --from` 只搬运显式列出的路径**:"You can selectively copy artifacts from one stage to another, leaving behind everything you don't want in the final image."
3. **最终镜像的"薄"是"没被引用"而不是"被清理"**:官方原文 —— "The Go SDK and any intermediate artifacts are left behind, and not saved in the final image." 构建阶段的层依然在 builder 的缓存里,只是最终镜像的 manifest 不引用它们。
4. **`--from` 既能指向阶段也能指向镜像**:`COPY --from=nginx:latest /etc/nginx/nginx.conf /nginx.conf` 会自动把该镜像拉下来再拷贝。
5. **`FROM <stage>` 复用前一阶段当基础镜像**:官方强调 `FROM` 与 `--from` 的参数解析规则相同 —— "They both take the same argument, resolve it and then either start a new stage from that point or use it as a source for file copy."
6. **`--target` 停在哪**:legacy builder "processes all stages of a Dockerfile leading up to the selected `--target`. It will build a stage even if the selected target doesn't depend on that stage.";BuildKit "only builds the stages that the target stage depends on"。官方例子:`--target stage2` 时 BuildKit 只处理 `base` + `stage2`,`stage1` 被跳过。
7. **`--from` 的值不能含构建参数**:`COPY --from=build-${src}` 非法 —— "the dependencies between the stages need to be determined before the build can start"。改造办法是先 `FROM build-${src} AS alias`,再 `COPY --from=alias`。
8. **`--cache-to` 的 `mode`**:`mode=min` 只导出"被导出进最终镜像的层";`mode=max` "exports every layer even those of intermediate steps"。也就是说共享缓存前要意识到**构建阶段的源码树也可能被导出**。

### 阶段依赖与剪枝

```
Dockerfile:                       BuildKit --target stage2 实际构建:
FROM alpine AS base               ┌── base ──┐
RUN apk add git                   │          │
FROM base AS builder              │        builder
RUN apk add build-base            │          │
FROM builder AS stage1            │        stage2      ← stage1 被跳过
RUN build s1                      └──────────┘
FROM builder AS stage2
RUN build s2

legacy builder:base → builder → stage1 → stage2(4 个阶段全跑)
```

## 对比 / 选型

| 维度 | 单阶段 | 多阶段 |
| --- | --- | --- |
| 最终镜像内容 | 基础镜像 + 工具链 + 源码 + 产物 | 基础镜像 + 产物 |
| 需要额外脚本 | 需要(两个 Dockerfile + `docker cp`) | 不需要,单文件 |
| 攻击面 | 有编译器 → 可重新编译 payload | 无编译器 |
| 中层缓存 | — | `mode=max` 可跨构建复用编译产物 |
| 调试中间阶段 | 无 | `--target <stage>` + `docker build -t dbg --target build .` |
| 潜在信息泄露 | 源码在镜像里 | 源码不在 **manifest** 里,但可能在**缓存**里 |

体积上的数量级:运行阶段若用 `scratch` 或 `gcr.io/distroless/static`,基础层只有约 2 MB 量级,加上静态二进制(~10–30 MB)通常落在几十 MB 以内;而带完整 Go 工具链的构建镜像常在 GB 量级。二者差距就是"被留在构建阶段"的部分。

## 环境准备

- 操作系统:任意(纯逻辑模拟,不需要 Docker)
- 语言:Python 3.10+、Go 1.21+
- 依赖:无第三方库

## 运行方式

### Python

```bash
python3 multi_stage.py
```

### Go

```bash
cd go && go run .
```

## 关键代码片段

```python
def build_stage(stage: Stage, built: dict[str, Image]) -> Image:
    # 规则 5:FROM 的参数既可以是镜像,也可以是更早的 stage
    img = resolve_base(stage.base, built)
    for st in stage.steps:
        if st.kind == "COPY" and st.from_stage is not None:
            if st.from_stage not in built:
                raise KeyError(f"COPY --from={st.from_stage} 引用了不存在或不更早的阶段")
            src = built[st.from_stage]
            missing = [s for s, _d in st.copies if s not in src.files]
            if missing:
                raise FileNotFoundError(f"源阶段没有 {missing}")
            # 规则 2:只搬指定路径 —— 工具链/源码树不跟着走
            for s, d in st.copies:
                img.files.add(d)
            img.size_mb += st.adds_mb
        else:
            img.files |= set(st.adds)
            img.size_mb += st.adds_mb
    return img


def reachable(stages: list[Stage], target: str) -> list[str]:
    """规则 6:BuildKit 只构建 target 依赖的阶段。依赖边 = FROM + COPY --from。"""
    ...
    deps = {st.base} | {s.from_stage for s in st.steps if s.from_stage}
```

## 性能与边界

- **阶段数不改变最终镜像层数**:最终镜像只由 target 阶段的指令构成,所以把构建阶段拆细不会让运行镜像变大。
- **剪枝收益与无关阶段数成正比**:官方例子中 `--target stage2` 时 BuildKit 比 legacy 少跑 1 个阶段;无关阶段越多、越贵,差距越大。
- **构建产物跨构建复用**:`--cache-to ... --cache-to-mode=max` 把中间层一并导出后,只改运行阶段的构建可以复用编译结果。
- **缓存位置是边界条件**:`mode=max` 意味着**构建阶段的完整源码树**也会进到缓存里;把这种缓存推到公共 registry 需要额外的信任评估。
- **`COPY --from` 的粒度是路径**:搬一个目录会连同其中所有内容一起搬,无法"只搬某个文件的一部分"。
- **调试用镜像不应进生产**:`--target build` 打出来的镜像常带 shell 与调试工具,适合排障但不适合发布。

## 注意事项与常见坑

1. **把"多阶段"等同于"安全"**:官方博客的说法是"smaller container images with better caching and **smaller security footprint**",但 `mode=max` 缓存会保留构建阶段的源码与私有依赖 —— 安全收益只在**最终镜像**这一侧成立。
2. **`COPY --from` 写错源路径**:源阶段里产物在 `/out/app`、目标写成 `/app` 是**正常**的(源/目标各写各的);但若源路径不存在会直接构建失败,报错信息是"源里没有这个文件",而不是"目标路径"。
3. **`--target` 只影响构建范围,不影响最终 tag**:`docker build --target build -t hello .` 会把**中间阶段**打成 `hello`,别误把它当发布镜像。
4. **`--from` 里塞构建参数**:`COPY --from=build-${src}` 一定失败;正确写法是先用一个只有 `FROM` 的别名阶段转换。
5. **`FROM <stage>` 之后忘记它在最终镜像里**:`FROM builder AS build1` 会让 `build1` **继承整个 builder 文件系统**;若这不是本意,应 `FROM <原镜像>` 再 `COPY --from=builder` 挑文件。
6. **distroless/scratch 没有 shell**:搬过去之后 `RUN` 就没法用了,所以"最后一公里"必须是 `COPY` 而不是 `RUN`。
7. **动态链接的二进制搬到 scratch 会 `exec format error` / 找不到解释器**:构建时用 `CGO_ENABLED=0 GOOS=linux`(或静态链接)才能放进空镜像。
8. **stage 名与镜像名可能撞车**:`Stage` 名解析优先于镜像名,起名时避免和公共镜像同名(例如把阶段叫 `alpine`)。

## 参考资料(实际阅读过的权威来源)

- [Docker Docs — Multi-stage builds](https://docs.docker.com/build/building/multi-stage/) — 阶段命名、`--target`、`COPY --from=<外部镜像>`、`FROM <stage>` 复用、legacy 与 BuildKit 在阶段处理范围上的差异
- [Docker Docs(旧版镜像)— Use multi-stage builds](https://docs.docker.com/develop/develop-images/multistage-build/) — 与上一页同一主题的历史版本,含 "The Go SDK and any intermediate artifacts are left behind" 原文与 `--target` 的三种用法场景
- [Docker Blog — Advanced Dockerfiles: Faster Builds and Smaller Images Using BuildKit and Multistage Builds](https://www.docker.com/?p=25914) — "Inheriting from a stage"、"BuildKit efficiently skips unused stages and builds stages concurrently"、`--from` 不可含构建参数及其原因
- [runbook.academy — Multi-stage builds: separating build from runtime](https://runbook.academy/courses/docker/lessons/docker-multi-stage-builds) — distroless static 约 2 MB / Go 工具链 1 GB+ 的量级,以及 "build stage 不在 manifest 里但在 content store 里"、`--cache-to` min/max 语义
