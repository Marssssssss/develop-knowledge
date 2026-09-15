// 多阶段构建(multi-stage build)的最小模拟器 —— Go 实现。
//
// 规则依据:
//   https://docs.docker.com/build/building/multi-stage/
//   https://www.docker.com/?p=25914   (Advanced Dockerfiles,BuildKit 跳过无关阶段)
//
// 模拟三件事:
//  1. 最终镜像 = target stage 的文件系统(构建阶段的工具链/源码不进最终镜像)
//  2. COPY --from=<stage|镜像> 只搬运指定路径
//  3. --target 下 legacy builder 处理 target 之前的全部阶段,BuildKit 只处理依赖的阶段
package main

import (
	"fmt"
	"sort"
	"strings"
)

// MB 只用于"量级示意",断言只针对关系而不针对真实字节数。
const MB = 1_000_000

var imageSizesMB = map[string]int{
	"golang:1.24":                        800, // 含完整 Go 工具链
	"gcr.io/distroless/static:nonroot":   2,
	"alpine:3.20":                        8,
	"scratch":                            0,
}

var toolchainMarkers = map[string]bool{"go": true, "gcc": true, "make": true, "git": true}

// PathPair 是 COPY --from 的 (源路径, 目标路径)。
type PathPair struct{ Src, Dst string }

// Step 是一条指令。
type Step struct {
	Kind   string // COPY / RUN
	Text   string
	Adds   []string
	AddsMB int
	From   string // COPY --from=<stage>
	Copies []PathPair
}

// Stage 是一个构建阶段。
type Stage struct {
	Name  string
	Base  string // 镜像名,或另一个 stage 名
	Steps []Step
}

// Image 是一个 stage 的产物。
type Image struct {
	Files  map[string]bool
	SizeMB int
}

func (i Image) clone() Image {
	f := make(map[string]bool, len(i.Files))
	for k := range i.Files {
		f[k] = true
	}
	return Image{Files: f, SizeMB: i.SizeMB}
}

func (i Image) hasToolchain() bool {
	for k := range i.Files {
		if toolchainMarkers[k] {
			return true
		}
	}
	return false
}

func (i Image) sortedFiles() string {
	out := make([]string, 0, len(i.Files))
	for k := range i.Files {
		out = append(out, k)
	}
	sort.Strings(out)
	return strings.Join(out, " ")
}

func baseFiles(base string) map[string]bool {
	switch {
	case base == "scratch":
		return map[string]bool{}
	case base == "gcr.io/distroless/static:nonroot":
		return map[string]bool{"etc/passwd": true, "etc/ssl/certs": true}
	case strings.HasPrefix(base, "golang:"):
		return map[string]bool{"go": true, "gcc": true, "make": true, "git": true, "/usr/local/go": true}
	default:
		return map[string]bool{"bin/sh": true}
	}
}

func baseSize(base string) int {
	if n, ok := imageSizesMB[base]; ok {
		return n
	}
	return 20
}

// resolveBase 体现官方那句"FROM 与 COPY --from 取同样的参数":
// 既能解析成镜像,也能解析成已构建的 stage。
func resolveBase(base string, built map[string]Image) (Image, error) {
	if img, ok := built[base]; ok {
		return img.clone(), nil
	}
	if _, ok := imageSizesMB[base]; !ok {
		return Image{}, fmt.Errorf("unknown base image: %s", base)
	}
	return Image{Files: baseFiles(base), SizeMB: baseSize(base)}, nil
}

// BuildStage 构建单个阶段。
func BuildStage(s Stage, built map[string]Image) (Image, error) {
	img, err := resolveBase(s.Base, built)
	if err != nil {
		return Image{}, err
	}
	for _, st := range s.Steps {
		if st.Kind == "COPY" && st.From != "" {
			src, ok := built[st.From]
			if !ok {
				return Image{}, fmt.Errorf("COPY --from=%s 引用了不存在或不更早的阶段", st.From)
			}
			for _, p := range st.Copies {
				if !src.Files[p.Src] {
					return Image{}, fmt.Errorf("源阶段没有 %s", p.Src)
				}
				img.Files[p.Dst] = true
			}
		} else {
			for _, p := range st.Adds {
				img.Files[p] = true
			}
		}
		img.SizeMB += st.AddsMB
	}
	return img, nil
}

// BuildAll 按 Dockerfile 顺序构建全部阶段。
func BuildAll(stages []Stage) (map[string]Image, error) {
	built := map[string]Image{}
	for _, s := range stages {
		img, err := BuildStage(s, built)
		if err != nil {
			return nil, err
		}
		built[s.Name] = img
	}
	return built, nil
}

// Reachable 是 BuildKit 语义:只构建 target 直接/间接依赖的阶段。
func Reachable(stages []Stage, target string) ([]string, error) {
	byName := map[string]Stage{}
	order := []string{}
	for _, s := range stages {
		byName[s.Name] = s
		order = append(order, s.Name)
	}
	if _, ok := byName[target]; !ok {
		return nil, fmt.Errorf("unknown stage: %s", target)
	}
	seen := map[string]bool{}
	stack := []string{target}
	for len(stack) > 0 {
		cur := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		if seen[cur] {
			continue
		}
		seen[cur] = true
		// 依赖来源:FROM <另一个 stage> 或 COPY --from=<另一个 stage>
		deps := []string{byName[cur].Base}
		for _, st := range byName[cur].Steps {
			if st.From != "" {
				deps = append(deps, st.From)
			}
		}
		for _, d := range deps {
			if _, ok := byName[d]; ok && !seen[d] {
				stack = append(stack, d)
			}
		}
	}
	out := []string{}
	for _, n := range order {
		if seen[n] {
			out = append(out, n)
		}
	}
	return out, nil
}

func singleStage() []Stage {
	return []Stage{{Name: "final", Base: "golang:1.24", Steps: []Step{
		{Kind: "COPY", Text: "COPY . .", Adds: []string{"src/", "go.mod"}, AddsMB: 2},
		{Kind: "RUN", Text: "go build -o /app ./src", Adds: []string{"/app"}, AddsMB: 12},
	}}}
}

func multiStage() []Stage {
	return []Stage{
		{Name: "build", Base: "golang:1.24", Steps: []Step{
			{Kind: "COPY", Text: "COPY . .", Adds: []string{"src/", "go.mod"}, AddsMB: 2},
			{Kind: "RUN", Text: "go build -o /out/app ./src", Adds: []string{"/out/app"}, AddsMB: 12},
		}},
		{Name: "runtime", Base: "gcr.io/distroless/static:nonroot", Steps: []Step{
			{Kind: "COPY", Text: "COPY --from=build /out/app /app", From: "build", AddsMB: 12,
				Copies: []PathPair{{Src: "/out/app", Dst: "/app"}}},
		}},
	}
}

func branched() []Stage {
	return []Stage{
		{Name: "base", Base: "alpine:3.20", Steps: []Step{
			{Kind: "RUN", Text: "apk add git", Adds: []string{"git"}, AddsMB: 20}}},
		{Name: "builder", Base: "base", Steps: []Step{
			{Kind: "RUN", Text: "apk add build-base", Adds: []string{"gcc"}, AddsMB: 30}}},
		{Name: "stage1", Base: "builder", Steps: []Step{
			{Kind: "RUN", Text: "build s1", Adds: []string{"/s1"}, AddsMB: 5}}},
		{Name: "stage2", Base: "builder", Steps: []Step{
			{Kind: "RUN", Text: "build s2", Adds: []string{"/s2"}, AddsMB: 7}}},
	}
}

func main() {
	one, err := BuildAll(singleStage())
	if err != nil {
		panic(err)
	}
	multi, err := BuildAll(multiStage())
	if err != nil {
		panic(err)
	}
	single, runtime := one["final"], multi["runtime"]

	fmt.Println("== 多阶段构建模拟(Go) ==")
	fmt.Printf("单阶段最终镜像(%d MB): %s\n", single.SizeMB, single.sortedFiles())
	fmt.Printf("多阶段最终镜像(%d MB): %s\n", runtime.SizeMB, runtime.sortedFiles())
	fmt.Printf("单阶段含工具链=%v  多阶段含工具链=%v  多阶段含源码树=%v\n",
		single.hasToolchain(), runtime.hasToolchain(), runtime.Files["src/"])
	fmt.Printf("体积差 = %d MB\n", single.SizeMB-runtime.SizeMB)

	// --target 语义:BuildKit 只构建依赖的阶段
	stages := branched()
	bk, err := Reachable(stages, "stage2")
	if err != nil {
		panic(err)
	}
	legacy := []string{}
	for _, s := range stages {
		legacy = append(legacy, s.Name)
		if s.Name == "stage2" {
			break
		}
	}
	fmt.Printf("BuildKit 构建阶段: %v\n", bk)
	fmt.Printf("legacy 构建阶段  : %v\n", legacy)

	// 阶段引用校验
	_, err = BuildAll([]Stage{{Name: "runtime", Base: "scratch", Steps: []Step{
		{Kind: "COPY", Text: "COPY --from=nope /x /x", From: "nope",
			Copies: []PathPair{{Src: "/x", Dst: "/x"}}}}}})
	fmt.Printf("引用不存在阶段 -> %v\n", err)

	branchedImages, err := BuildAll(branched())
	if err != nil {
		panic(err)
	}
	fmt.Printf("stage1 继承 builder: %s (尺寸 %d MB)\n",
		branchedImages["stage1"].sortedFiles(), branchedImages["stage1"].SizeMB)

	// cache-to 语义:mode=max 连中间步骤层一起导出
	all, fin := 0, 0
	for _, s := range multiStage() {
		all += len(s.Steps)
	}
	fin = len(multiStage()[len(multiStage())-1].Steps)
	fmt.Printf("mode=max 导出 %d 层 / mode=min 导出 %d 层\n", all, fin)
}
