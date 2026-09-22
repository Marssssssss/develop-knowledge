// fs-verity Merkle 树与 dm-integrity 布局的 Go 侧镜像。
//
// 来源同 Python 侧：Documentation/filesystems/fsverity.html、
// include/uapi/linux/fsverity.h、
// Documentation/admin-guide/device-mapper/dm-integrity.html
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
)

const (
	fsVerityHashAlgSha256 = 1
	fsVerityHashAlgSha512 = 2

	fsVerityMaxSaltSize = 32

	fsverityDescriptorSize = 256
	fsverityRootHashSize   = 64

	descOffVersion      = 0
	descOffHashAlg      = 1
	descOffLogBlocksize = 2
	descOffSaltSize     = 3
	descOffDataSize     = 8
	descOffRootHash     = 16
	descOffSalt         = 80

	blockSize = 4096
	sha256DS  = 32

	// dm-integrity 默认值
	dmDefaultInterleaveSectors = 32768
	dmDefaultBufferSectors     = 128
	dmDefaultBlockSize         = 512
	sectorSize                 = 512
)

// hashAlgBlocksize 是各算法压缩函数的输入块大小（salt 要填充到它的整数倍）。
var hashAlgBlocksize = map[int]int{
	fsVerityHashAlgSha256: 64,
	fsVerityHashAlgSha512: 128,
}

var hashAlgDigestsize = map[int]int{
	fsVerityHashAlgSha256: 32,
	fsVerityHashAlgSha512: 64,
}

// PadSalt 把 salt 零填充到压缩函数输入块的整数倍。
func PadSalt(salt []byte, alg int) []byte {
	if len(salt) == 0 {
		return nil
	}
	bs := hashAlgBlocksize[alg]
	n := (len(salt) + bs - 1) / bs * bs
	out := make([]byte, n)
	copy(out, salt)
	return out
}

// Arity 是每个块能装的下层 hash 个数。
func Arity(blockSize, digestSize int) int { return blockSize / digestSize }

// MerkleLevelSizes 返回每层有多少块，第 0 层是数据块。
func MerkleLevelSizes(dataSize, blockSize, digestSize int) []int {
	if dataSize == 0 {
		return nil
	}
	nblocks := (dataSize + blockSize - 1) / blockSize
	levels := []int{nblocks}
	hashes := nblocks * digestSize
	for nblocks != 1 {
		nblocks = (hashes + blockSize - 1) / blockSize
		levels = append(levels, nblocks)
		hashes = nblocks * digestSize
	}
	return levels
}

// MerkleTreeOverhead 是 Merkle 树本身的字节数（不含 descriptor）。
func MerkleTreeOverhead(dataSize, blockSize, digestSize int) int {
	lv := MerkleLevelSizes(dataSize, blockSize, digestSize)
	if len(lv) < 2 {
		return 0
	}
	sum := 0
	for _, n := range lv[1:] {
		sum += n * blockSize
	}
	return sum
}

func sha256Of(b []byte) []byte {
	s := sha256.Sum256(b)
	return s[:]
}

// BuildDescriptor 序列化 struct fsverity_descriptor（小端）。
func BuildDescriptor(dataSize, logBlocksize, alg int, rootHash, salt []byte) []byte {
	d := make([]byte, fsverityDescriptorSize)
	d[descOffVersion] = 1
	d[descOffHashAlg] = byte(alg)
	d[descOffLogBlocksize] = byte(logBlocksize)
	d[descOffSaltSize] = byte(len(salt))
	for i := 0; i < 8; i++ {
		d[descOffDataSize+i] = byte(uint64(dataSize) >> uint(8*i))
	}
	copy(d[descOffRootHash:descOffRootHash+len(rootHash)], rootHash)
	copy(d[descOffSalt:descOffSalt+len(salt)], salt)
	return d
}

// FileDigest 是 hash(descriptor)，不是 Merkle root。
func FileDigest(descriptor []byte) []byte { return sha256Of(descriptor) }

// BuildMerkleRoot 建树并返回 root 与层数。
func BuildMerkleRoot(data []byte, bs, alg int) ([]byte, int) {
	ds := hashAlgDigestsize[alg]
	if len(data) == 0 {
		return make([]byte, ds), 0
	}
	n0 := (len(data) + bs - 1) / bs
	cur := make([][]byte, 0, n0)
	for i := 0; i < n0; i++ {
		b := make([]byte, bs)
		copy(b, data[i*bs:min(i*bs+bs, len(data))])
		cur = append(cur, b)
	}
	levels := 1
	for {
		var digests []byte
		for _, b := range cur {
			digests = append(digests, sha256Of(b)...)
		}
		if len(cur) == 1 {
			return digests[:ds], levels
		}
		packed := make([][]byte, 0)
		for i := 0; i < len(digests); i += bs {
			b := make([]byte, bs)
			copy(b, digests[i:min(i+bs, len(digests))])
			packed = append(packed, b)
		}
		cur = packed
		levels++
	}
}

// RounddownPow2 用于 interleave_sectors / buffer_sectors 的向下取整。
func RounddownPow2(x int) int {
	if x <= 0 {
		return 0
	}
	return 1 << (bits(x) - 1)
}

func bits(x int) int {
	n := 0
	for x > 0 {
		x >>= 1
		n++
	}
	return n
}

// Geometry 是一个 interleave run 的扇区布局。
type Geometry struct {
	TagBytes      int
	TagSectors    int
	DataSectors   int
	BuffersPerRun int
	Overhead      float64
}

// IntegrityGeometry 计算 dm-integrity 的标签区/数据区布局。
func IntegrityGeometry(interleaveSectors, blockSize, tagSize, bufferSectors int) Geometry {
	il := RounddownPow2(interleaveSectors)
	tagBytes := il * tagSize
	tagSectors := (tagBytes + sectorSize - 1) / sectorSize
	if tagSectors < 8 { // 标签区至少 4 KiB
		tagSectors = 8
	}
	dataSectors := RounddownPow2(il - tagSectors)
	bufs := (tagSectors + bufferSectors - 1) / bufferSectors
	return Geometry{
		TagBytes:      tagBytes,
		TagSectors:    tagSectors,
		DataSectors:   dataSectors,
		BuffersPerRun: bufs,
		Overhead:      float64(tagSectors) / float64(tagSectors+dataSectors),
	}
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

func main() {
	fmt.Println("== salt 零填充（填充到压缩函数输入块的整数倍） ==")
	for _, n := range []int{0, 13, 32, 64, 65} {
		s := make([]byte, n)
		fmt.Printf("  %-3d 字节(SHA-256) → %-4d 字节\n", n, len(PadSalt(s, fsVerityHashAlgSha256)))
	}
	fmt.Printf("  13  字节(SHA-512) → %-4d 字节\n",
		len(PadSalt(make([]byte, 13), fsVerityHashAlgSha512)))

	fmt.Println("== 扇出与层级 ==")
	fmt.Printf("  SHA-256 + 4K 块 → arity %d\n", Arity(blockSize, sha256DS))
	fmt.Printf("  SHA-512 + 4K 块 → arity %d\n", Arity(blockSize, 64))
	for _, n := range []int{1, 2, 128, 129, 16384} {
		fmt.Printf("  %-6d 块 → %v\n", n,
			MerkleLevelSizes(n*blockSize, blockSize, sha256DS))
	}

	fmt.Println("== 1/127 收敛 ==")
	for _, n := range []int{2, 128, 16384, 128 * 128 * 128} {
		size := n * blockSize
		oh := MerkleTreeOverhead(size, blockSize, sha256DS)
		fmt.Printf("  %-8d 块 → 树 %-9d 字节，占比 %.4f\n", n, oh, float64(oh)/float64(size))
	}

	fmt.Println("== Merkle root ==")
	cases := []struct {
		name string
		data []byte
	}{
		{"空文件", nil},
		{"1 块", []byte("hello fs-verity")},
		{"2 块", make([]byte, blockSize+10)},
	}
	for _, c := range cases {
		root, lv := BuildMerkleRoot(c.data, blockSize, fsVerityHashAlgSha256)
		fmt.Printf("  %-6s → root=%s... 层数=%d\n", c.name, hex.EncodeToString(root)[:24], lv)
	}
	two := make([]byte, blockSize+10)
	root, _ := BuildMerkleRoot(two, blockSize, fsVerityHashAlgSha256)
	desc := BuildDescriptor(len(two), 12, fsVerityHashAlgSha256, root, nil)
	fmt.Printf("  文件摘要 = sha256(descriptor) = %s...\n",
		hex.EncodeToString(FileDigest(desc))[:24])

	fmt.Println("== dm-integrity 几何 ==")
	for _, cfg := range [][3]int{{512, 4}, {512, 16}, {4096, 32}} {
		g := IntegrityGeometry(dmDefaultInterleaveSectors, cfg[0], cfg[1], dmDefaultBufferSectors)
		fmt.Printf("  block=%-5d tag=%-3d → 标签 %-5d 扇区, 数据 %-6d 扇区,"
			" buffer/区 %-3d 开销 %.4f\n",
			cfg[0], cfg[1], g.TagSectors, g.DataSectors, g.BuffersPerRun, g.Overhead)
	}
}
