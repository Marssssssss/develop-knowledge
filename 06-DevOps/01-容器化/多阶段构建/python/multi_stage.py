#!/usr/bin/env python3
"""多阶段构建(multi-stage build)的最小模拟器。

规则依据 Docker 官方文档 "Multi-stage builds":
  https://docs.docker.com/build/building/multi-stage/
以及 Docker 官方博客 "Advanced Dockerfiles"(BuildKit 跳过无关阶段):
  https://www.docker.com/?p=25914

三件被模拟的事:
  1. 每个 FROM 开启一个 stage;最终镜像 = target stage 的文件系统
     (构建阶段的东西不进最终镜像,因为"根本没被 manifest 引用")
  2. COPY --from=<stage|镜像> 只搬运指定路径,不搬运工具链
  3. --target 下:legacy builder 处理 target 之前的**全部**阶段,
     BuildKit 只处理 target **依赖**的阶段

尺寸常量是"量级示意"(见 README 的说明),断言只针对**关系**而非真实字节数。

自检:python3 multi_stage.py
"""
from __future__ import annotations

from dataclasses import dataclass, field

MB = 1_000_000

# 量级示意表(单位 MB)。取自公开资料的数量级,用于演示"工具链 vs 产物"的对比。
IMAGE_SIZES = {
    "golang:1.24": 800,            # 含完整 Go 工具链
    "gcr.io/distroless/static:nonroot": 2,   # distroless static 底
    "alpine:3.20": 8,
    "scratch": 0,                  # 空镜像
}
TOOLCHAIN_MARKERS = {"go", "gcc", "make", "git"}   # 出现即说明"编译环境进了镜像"
SRC_MARKERS = {"src/"}


@dataclass
class Step:
    kind: str            # COPY / RUN
    text: str
    adds: set[str] = field(default_factory=set)      # 本步新增的文件路径
    adds_mb: int = 0                                 # 本步新增的字节量级
    from_stage: str | None = None                    # COPY --from
    copies: list[tuple[str, str]] = field(default_factory=list)  # (源路径, 目标路径)


@dataclass
class Stage:
    name: str
    base: str                    # 镜像名,或另一个 stage 名
    steps: list[Step] = field(default_factory=list)


@dataclass
class Image:
    """一个 stage 的产物:文件系统 + 字节量级。"""
    files: set[str]
    size_mb: int


def base_files(base: str) -> set[str]:
    if base == "scratch":
        return set()
    if base == "gcr.io/distroless/static:nonroot":
        return {"etc/passwd", "etc/ssl/certs"}
    if base.startswith("golang:"):
        return set(TOOLCHAIN_MARKERS) | {"/usr/local/go"}
    return {"bin/sh"}


def base_size(base: str) -> int:
    return IMAGE_SIZES.get(base, 20)


def resolve_base(base: str, built: dict[str, Image]) -> Image:
    """FROM 的参数与 COPY --from 的参数同名同义:既可以是镜像,也可以是已构建的 stage。"""
    if base in built:
        img = built[base]
        return Image(files=set(img.files), size_mb=img.size_mb)
    if base not in IMAGE_SIZES:
        raise KeyError(f"unknown base image: {base}")
    return Image(files=base_files(base), size_mb=base_size(base))


def build_stage(stage: Stage, built: dict[str, Image]) -> Image:
    img = resolve_base(stage.base, built)
    for st in stage.steps:
        if st.kind == "COPY" and st.from_stage is not None:
            if st.from_stage not in built:
                raise KeyError(f"COPY --from={st.from_stage} 引用了不存在或不更早的阶段")
            src = built[st.from_stage]
            missing = [s for s, _d in st.copies if s not in src.files]
            if missing:
                raise FileNotFoundError(f"源阶段没有 {missing}")
            # 只搬指定路径:源阶段的工具链、源码树都不跟着走
            for s, d in st.copies:
                img.files.add(d)
            img.size_mb += st.adds_mb
        else:
            img.files |= set(st.adds)
            img.size_mb += st.adds_mb
    return img


def reachable(stages: list[Stage], target: str) -> list[str]:
    """BuildKit 语义:只构建 target 直接/间接依赖的阶段。"""
    by_name = {s.name: s for s in stages}
    if target not in by_name:
        raise KeyError(f"unknown stage: {target}")
    need: list[str] = []
    stack = [target]
    while stack:
        cur = stack.pop()
        if cur in need:
            continue
        need.append(cur)
        st = by_name[cur]
        # 依赖来源:FROM <另一个 stage> 或 COPY --from=<另一个 stage>
        deps = {st.base} | {s.from_stage for s in st.steps if s.from_stage}
        for d in deps:
            if d in by_name:
                stack.append(d)
    # 按 Dockerfile 出现顺序返回
    order = [s.name for s in stages]
    return [n for n in order if n in need]


# --------------------------------------------------------------------------
# 两个 Dockerfile
# --------------------------------------------------------------------------
def single_stage() -> list[Stage]:
    """只用一条 FROM:编译工具链和源码都留在最终镜像里。"""
    return [Stage("final", "golang:1.24", [
        Step("COPY", "COPY . .", adds={"src/", "go.mod"}, adds_mb=2),
        Step("RUN", "CGO_ENABLED=0 go build -o /app ./src", adds={"/app"}, adds_mb=12),
    ])]


def multi_stage() -> list[Stage]:
    """构建阶段装工具链,运行阶段只拿产物。"""
    return [
        Stage("build", "golang:1.24", [
            Step("COPY", "COPY . .", adds={"src/", "go.mod"}, adds_mb=2),
            Step("RUN", "go build -o /out/app ./src", adds={"/out/app"}, adds_mb=12),
        ]),
        Stage("runtime", "gcr.io/distroless/static:nonroot", [
            Step("COPY", "COPY --from=build /out/app /app", from_stage="build",
                 copies=[("/out/app", "/app")], adds_mb=12),
        ]),
    ]


def branched(shared: str = "builder") -> list[Stage]:
    """共用一个 base 分支出两个阶段(官方博客 "Inheriting from a stage" 的例子)。

    注意 builder 的 base 是 **base 这个 stage**,不是镜像名 ——
    这正是官方强调的"FROM 与 COPY --from 取同样的参数"。
    """
    return [
        Stage("base", "alpine:3.20", [Step("RUN", "apk add git", adds={"git"}, adds_mb=20)]),
        Stage(shared, "base", [Step("RUN", "apk add build-base", adds={"gcc"}, adds_mb=30)]),
        Stage("stage1", shared, [Step("RUN", "build s1", adds={"/s1"}, adds_mb=5)]),
        Stage("stage2", shared, [Step("RUN", "build s2", adds={"/s2"}, adds_mb=7)]),
    ]


# --------------------------------------------------------------------------
# 自检
# --------------------------------------------------------------------------
OK = 0
FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  [PASS] {label} {detail}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


def build_all(stages: list[Stage]) -> dict[str, Image]:
    built: dict[str, Image] = {}
    for st in stages:
        built[st.name] = build_stage(st, built)
    return built


def main() -> int:
    print("=== 1. 单阶段 vs 多阶段:最终镜像里有什么 ===")
    one = build_all(single_stage())
    multi = build_all(multi_stage())
    single_img, runtime_img = one["final"], multi["runtime"]

    check("单阶段最终镜像含编译工具链",
          {"go", "make"} <= single_img.files, sorted(single_img.files))
    check("多阶段最终镜像不含编译工具链",
          not (TOOLCHAIN_MARKERS & runtime_img.files), sorted(runtime_img.files))
    check("多阶段最终镜像不含源码树",
          "src/" not in runtime_img.files, sorted(runtime_img.files))
    check("单阶段最终镜像含源码树", "src/" in single_img.files)
    print(f"  单阶段 {single_img.size_mb} MB vs 多阶段 {runtime_img.size_mb} MB")

    print("\n=== 2. 体积差 = 被留在构建阶段的工具链 ===")
    delta = single_img.size_mb - runtime_img.size_mb
    toolchain = sum(IMAGE_SIZES["golang:1.24"] - IMAGE_SIZES["gcr.io/distroless/static:nonroot"]
                    for _ in [0])
    check("体积差等于基础镜像中工具链部分 + 源码树",
          delta == toolchain + 2, f"Δ={delta} MB(工具链 {toolchain} MB + 源码 2 MB)")

    print("\n=== 3. --target:BuildKit 只构建依赖的阶段 ===")
    stages = branched()
    all_names = [s.name for s in stages]
    bk = reachable(stages, "stage2")
    legacy = all_names[:all_names.index("stage2") + 1]   # legacy 处理 target 之前的全部阶段
    check("BuildKit 跳过无关的 stage1", bk == ["base", "builder", "stage2"], str(bk))
    check("legacy 仍会构建 stage1", "stage1" in legacy, str(legacy))
    check("BuildKit 少构建一个阶段", len(legacy) - len(bk) == 1,
          f"legacy {len(legacy)} 个阶段 vs BuildKit {len(bk)} 个")

    print("\n=== 4. COPY --from 只搬指定路径 ===")
    built = build_all(multi_stage())
    src_stage = built["build"]
    check("构建阶段自己有工具链和源码",
          "go" in src_stage.files and "src/" in src_stage.files)
    check("搬过来只有 /app", built["runtime"].files == {"/app", "etc/passwd", "etc/ssl/certs"},
          sorted(built["runtime"].files))
    check("搬运大小只算产物", built["runtime"].size_mb == 2 + 12, f"{built['runtime'].size_mb} MB")

    print("\n=== 5. FROM <stage> 与 COPY --from=<stage> 同参同义 ===")
    b = build_all(branched())
    check("FROM builder 继承了 gcc", "gcc" in b["stage1"].files, sorted(b["stage1"].files))
    check("stage1 与 stage2 各自独立累加",
          b["stage1"].size_mb == b["builder"].size_mb + 5 and
          b["stage2"].size_mb == b["builder"].size_mb + 7,
          f"{b['stage1'].size_mb} / {b['stage2'].size_mb}")

    print("\n=== 6. 引用不存在的阶段 / 路径必须报错 ===")
    bad = [Stage("runtime", "scratch",
                 [Step("COPY", "COPY --from=nope /x /x", from_stage="nope",
                       copies=[("/x", "/x")])])]
    try:
        build_all(bad)
        check("引用不存在阶段应报错", False, "没有报错")
    except KeyError as exc:
        check("引用不存在阶段应报错", "nope" in str(exc), str(exc))

    bad2 = [Stage("b", "scratch", [Step("RUN", "echo hi", adds={"/hi"})]),
            Stage("r", "scratch",
                 [Step("COPY", "COPY --from=b /nope /nope", from_stage="b",
                       copies=[("/nope", "/nope")])])]
    try:
        build_all(bad2)
        check("源阶段缺该路径应报错", False, "没有报错")
    except FileNotFoundError as exc:
        check("源阶段缺该路径应报错", "/nope" in str(exc), str(exc))

    print("\n=== 7. cache-to mode=min vs max 导出的层 ===")
    # 每个 stage 的每步产出一个层;mode=min 只导最终镜像用到的层
    layers_all = sum(len(s.steps) for s in multi_stage())
    layers_final = len(multi_stage()[-1].steps)
    check("mode=max 导出全部阶段的层", layers_all == 3, f"{layers_all} 层")
    check("mode=min 只导最终镜像的层", layers_final == 1, f"{layers_final} 层")
    check("差值就是构建阶段(含源码树)被导出的层", layers_all - layers_final == 2)

    print("\n=== 8. 攻击面:运行时镜像里有没有编译器 ===")
    def has_compiler(img: Image) -> bool:
        return bool(img.files & {"go", "gcc", "make"})
    check("单阶段:攻击者可重新编译 payload", has_compiler(single_img))
    check("多阶段:运行时镜像无编译器", not has_compiler(runtime_img))

    print(f"\n合计:{OK} passed / {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
