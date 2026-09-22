// 可复现构建（Reproducible Builds）的阻力与归一化模型。
//
// 依据 reproducible-builds.org：
//   - SOURCE_DATE_EPOCH 规范（值格式、必须用于嵌入时间戳、时间戳钳制是上界、
//     不得对子进程 unset、畸形值应非零退出）
//   - Timestamps 页（工具不支持时的后处理：strip-nondeterminism / libfaketime 的坑）
//   - Build path 页（-fdebug-prefix-map / -fmacro-prefix-map / -ffile-prefix-map）
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"sort"
	"strconv"
	"strings"
	"time"
)

// ZipEpochMin ZIP 的时间戳下界：1980-01-01 00:00:00 UTC
const ZipEpochMin = 315532800

// ParseSourceDateEpoch 按规范文档的 C 参考实现解析：纯十进制、无前后垃圾、不溢出
func ParseSourceDateEpoch(raw string) (int64, error) {
	if raw == "" {
		return 0, errors.New("No digits were found")
	}
	for i := 0; i < len(raw); i++ {
		if raw[i] < '0' || raw[i] > '9' {
			return 0, errors.New("Trailing garbage: " + raw[i:])
		}
	}
	value, err := strconv.ParseUint(raw, 10, 64)
	if err != nil {
		return 0, errors.New("value must be smaller than or equal to 18446744073709551615")
	}
	return int64(value), nil
}

// BuildTime 规范：构建进程的「当前时间」一律用 SOURCE_DATE_EPOCH 代替
func BuildTime(env map[string]string, wallClock int64) (int64, error) {
	raw, ok := env["SOURCE_DATE_EPOCH"]
	if !ok || raw == "" {
		return wallClock, nil
	}
	return ParseSourceDateEpoch(raw)
}

// ClampMtime 时间戳钳制（GNU tar 的 --clamp-mtime）：比 SDE 新的改写成 SDE。
// 注意是**上界**钳制，不是下界。
func ClampMtime(mtime, sde int64) int64 {
	if mtime <= sde {
		return mtime
	}
	return sde
}

// ZipDatetime ZIP 存不下 1980 年以前的时间，先抬下界再压上界
func ZipDatetime(mtime, sde int64) int64 {
	if mtime < ZipEpochMin {
		mtime = ZipEpochMin
	}
	return ClampMtime(mtime, sde)
}

// FormatDate %Y-%m-%d 的格式化受 TZ 影响
func FormatDate(mtime, tzOffset int64) string {
	return time.Unix(mtime+tzOffset, 0).UTC().Format("2006-01-02")
}

// LocaleKey C 区域按字节；en_US.UTF-8 近似为「忽略大小写与标点」
func LocaleKey(name, collation string) string {
	if collation == "C" {
		return name
	}
	var b strings.Builder
	for _, r := range strings.ToLower(name) {
		if (r >= 'a' && r <= 'z') || (r >= '0' && r <= '9') {
			b.WriteRune(r)
		}
	}
	return b.String()
}

// SortNames 按给定区域设置排序
func SortNames(names []string, collation string) []string {
	out := append([]string{}, names...)
	sort.SliceStable(out, func(i, j int) bool {
		return LocaleKey(out[i], collation) < LocaleKey(out[j], collation)
	})
	return out
}

// Entry 归档里的一个条目
type Entry struct {
	Name  string
	Data  string
	Mtime int64
	Mode  int
	UID   int
	GID   int
	Uname string
	Gname string
}

// ApplyUmask umask 会把位清掉，构建者的 umask 不同产物就不同
func ApplyUmask(mode, umask int) int {
	return mode & ^umask
}

// Normalize 归一化管线：钳制时间戳、清零属主、重映射构建路径、显式排序
func Normalize(entries []Entry, sde int64, buildPath, normalizedPath string) []Entry {
	out := make([]Entry, 0, len(entries))
	for _, e := range entries {
		n := e
		n.Mtime = ClampMtime(n.Mtime, sde)
		n.UID = 0
		n.GID = 0
		n.Uname = ""
		n.Gname = ""
		if buildPath != "" {
			if strings.HasPrefix(n.Name, buildPath) {
				n.Name = normalizedPath + strings.TrimPrefix(n.Name, buildPath)
			}
			n.Data = strings.ReplaceAll(n.Data, buildPath, normalizedPath)
		}
		out = append(out, n)
	}
	// 目录遍历顺序不稳定，必须显式排序；排序又受区域设置影响，所以按字节排
	sort.SliceStable(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	return out
}

// Digest 把条目序列压成一个摘要（真实场景就是产物字节的哈希）
func Digest(entries []Entry) string {
	h := sha256.New()
	for _, e := range entries {
		fmt.Fprintf(h, "%s\x00%s\x00%d\x00%o\x00%d\x00%d\x00%s\x00%s\x1f",
			e.Name, e.Data, e.Mtime, e.Mode&0o777, e.UID, e.GID, e.Uname, e.Gname)
	}
	return hex.EncodeToString(h.Sum(nil))
}

// Build 模拟一次构建，返回产物摘要
func Build(entries []Entry, env map[string]string, wallClock int64, umask int,
	buildPath string, collation string, doNormalize bool) (string, error) {
	sde, err := BuildTime(env, wallClock)
	if err != nil {
		return "", err
	}
	material := make([]Entry, 0, len(entries))
	byName := map[string]Entry{}
	for _, e := range entries {
		byName[e.Name] = e
	}
	for _, name := range SortNames(namesOf(entries), collation) {
		e := byName[name]
		e.Mode = ApplyUmask(e.Mode, umask)
		material = append(material, e)
	}
	if doNormalize {
		material = Normalize(material, sde, buildPath, "/build")
	}
	return Digest(material), nil
}

func namesOf(entries []Entry) []string {
	out := make([]string, 0, len(entries))
	for _, e := range entries {
		out = append(out, e.Name)
	}
	return out
}
