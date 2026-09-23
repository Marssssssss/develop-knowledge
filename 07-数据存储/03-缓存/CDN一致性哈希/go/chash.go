// nginx upstream consistent hash 的 Go 转写。
// 对应 nginx/nginx@master：
//   - src/core/ngx_crc32.c:34    crc32 表
//   - src/core/ngx_crc32.h:54-74 init/update/final
//   - src/http/modules/ngx_http_upstream_hash_module.c:337/490/585
package main

import "sort"

const Crc32Init = 0xFFFFFFFF

func buildCrc32Table() []uint32 {
	table := make([]uint32, 256)
	for n := 0; n < 256; n++ {
		c := uint32(n)
		for k := 0; k < 8; k++ {
			if c&1 != 0 {
				c = 0xEDB88320 ^ (c >> 1)
			} else {
				c = c >> 1
			}
		}
		table[n] = c
	}
	return table
}

var Crc32Table = buildCrc32Table()

func NgxCrc32Init() uint32 { return Crc32Init }

func NgxCrc32Update(crc uint32, data []byte) uint32 {
	c := crc
	for _, b := range data {
		c = Crc32Table[(c^uint32(b))&0xFF] ^ (c >> 8)
	}
	return c
}

func NgxCrc32Final(crc uint32) uint32 { return crc ^ 0xFFFFFFFF }

func NgxCrc32(data []byte) uint32 {
	return NgxCrc32Final(NgxCrc32Update(NgxCrc32Init(), data))
}

// SplitHostPort 复刻 update_chash 里的 host/port 切分：从尾部往前扫。
func SplitHostPort(server string) (string, string) {
	if len(server) >= 5 && lower(server[:5]) == "unix:" {
		return server[5:], ""
	}
	j := 0
	for j < len(server) {
		c := server[len(server)-j-1]
		if c == ':' {
			return server[:len(server)-j-1], server[len(server)-j:]
		}
		if c < '0' || c > '9' {
			break
		}
		j++
	}
	return server, ""
}

func lower(s string) string {
	b := []byte(s)
	for i := range b {
		if b[i] >= 'A' && b[i] <= 'Z' {
			b[i] += 'a' - 'A'
		}
	}
	return string(b)
}

type ChashPoint struct {
	Hash   uint32
	Server string
}

// BuildChashPoints 生成 weight*160 个点，排序并按 C 的 i/j 双指针去重。
func BuildChashPoints(servers []ChashServer, dedup bool) []ChashPoint {
	var points []ChashPoint
	for _, s := range servers {
		host, port := SplitHostPort(s.Name)
		base := NgxCrc32Init()
		base = NgxCrc32Update(base, []byte(host))
		base = NgxCrc32Update(base, []byte{0}) // ngx 传的是 "" 字面量、长度 1
		base = NgxCrc32Update(base, []byte(port))

		prev := uint32(0)
		for i := 0; i < s.Weight*160; i++ {
			pb := []byte{byte(prev), byte(prev >> 8), byte(prev >> 16), byte(prev >> 24)}
			h := NgxCrc32Final(NgxCrc32Update(base, pb))
			points = append(points, ChashPoint{Hash: h, Server: s.Name})
			prev = h
		}
	}

	sort.Slice(points, func(i, j int) bool { return points[i].Hash < points[j].Hash })

	if !dedup {
		return points
	}
	out := make([]ChashPoint, 0, len(points))
	for _, p := range points {
		if len(out) == 0 || out[len(out)-1].Hash != p.Hash {
			out = append(out, p)
		}
	}
	return out
}

type ChashServer struct {
	Name   string
	Weight int
}

// FindChashPoint 找第一个 point >= hash 的下标；可能等于 len(points)。
func FindChashPoint(points []ChashPoint, hash uint32) int {
	i, j := 0, len(points)
	for i < j {
		k := (i + j) / 2
		if hash > points[k].Hash {
			i = k + 1
		} else if hash < points[k].Hash {
			j = k
		} else {
			return k
		}
	}
	return i
}

func ChashLookup(points []ChashPoint, key string) string {
	if len(points) == 0 {
		return ""
	}
	h := NgxCrc32([]byte(key))
	i := FindChashPoint(points, h)
	if i == len(points) {
		i = 0
	}
	return points[i].Server
}
