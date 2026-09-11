// jbd2_sim.go — JBD2 journal 块格式 in-memory 模拟器
//
// 运行: go run jbd2_sim.go
//
// 演示:
//   1. 写入一笔事务: descriptor + data + commit,展示字节布局
//   2. Recovery:顺序扫描 journal,重放已 commit 事务,丢弃未 commit
//   3. JBD2 大端 vs ext4 小端:同一份数据两种序列化
//   4. JBD2_FLAG_ESCAPE

//go:build unix

package main

import (
	"encoding/binary"
	"fmt"
)

const (
	BLCKSIZE     = 4096
	JBD2Magic    = 0xC03B3998
	DescBlock    = 1
	CommitBlock  = 2
	RevokeBlock  = 5
	SameUUID     = 0x2
	Escape       = 0x1
	LastTag      = 0x8
)

func be32(n uint32) []byte {
	b := make([]byte, 4)
	binary.BigEndian.PutUint32(b, n)
	return b
}
func be32u(b []byte, off int) uint32 {
	return binary.BigEndian.Uint32(b[off:])
}

func makeDescriptorBlock(seq uint32, tags [][3]interface{}) []byte {
	blk := make([]byte, BLCKSIZE)
	copy(blk[0:], be32(JBD2Magic))
	copy(blk[4:], be32(DescBlock))
	copy(blk[8:], be32(seq))
	off := 12
	for i, t := range tags {
		bn := t[0].(uint32)
		flags := t[1].(uint32)
		uuid := t[2].([]byte)
		f := flags
		if i == len(tags)-1 {
			f |= LastTag
		}
		copy(blk[off:], be32(bn))
		off += 4
		copy(blk[off:], be32(f))
		off += 4
		if f&SameUUID == 0 {
			copy(blk[off:], uuid)
			off += 16
		}
	}
	return blk
}

func makeCommitBlock(seq uint32) []byte {
	blk := make([]byte, BLCKSIZE)
	copy(blk[0:], be32(JBD2Magic))
	copy(blk[4:], be32(CommitBlock))
	copy(blk[8:], be32(seq))
	return blk
}

func parseHeader(b []byte) (magic, btype, seq uint32) {
	magic = be32u(b, 0)
	btype = be32u(b, 4)
	seq = be32u(b, 8)
	return
}

func demo1WriteTransaction() {
	fmt.Println("\n=== demo 1: write a transaction (descriptor + data + commit) ===")
	seq := uint32(42)
	uuid := make([]byte, 16)
	uuid[0], uuid[1], uuid[2], uuid[3] = 0xAA, 0xBB, 0xCC, 0xDD
	uuid[15] = 0x01
	tags := [][3]interface{}{
		{uint32(256), uint32(SameUUID), uuid},
		{uint32(257), uint32(SameUUID), uuid},
	}
	desc := makeDescriptorBlock(seq, tags)
	data0 := make([]byte, BLCKSIZE)
	copy(data0, "INODE_BLOCK_FOR_FILE_42")
	data1 := make([]byte, BLCKSIZE)
	copy(data1, "DIRENT: hello.txt -> inode 42")
	commit := makeCommitBlock(seq)

	fmt.Printf("descriptor header bytes (12): % x\n", desc[:12])
	fmt.Println("  expect: c03b3998 (magic)  00000001 (descriptor)  0000002a (seq=42)")
	fmt.Printf("descriptor tags:\n")
	fmt.Printf("  tag 0: blocknr=%d, flags=0x%x\n", be32u(desc, 16), be32u(desc, 20))
	fmt.Printf("  tag 1: blocknr=%d, flags=0x%x  (LAST_TAG|SAME_UUID = 0x%x)\n",
		be32u(desc, 24), be32u(desc, 28), LastTag|SameUUID)
	fmt.Printf("commit header bytes (12): % x\n", commit[:12])
	fmt.Println("  expect: c03b3998  00000002  0000002a")
}

func demo2Replay() {
	fmt.Println("\n=== demo 2: replay committed, drop uncommitted ===")
	// T1 完整,T2 缺 commit
	t1 := makeDescriptorBlock(10, [][3]interface{}{{uint32(100), uint32(SameUUID), make([]byte, 16)}})
	d1 := make([]byte, BLCKSIZE)
	copy(d1, "T1_data_at_block_100")
	c1 := makeCommitBlock(10)
	t2 := makeDescriptorBlock(11, [][3]interface{}{{uint32(200), uint32(SameUUID), make([]byte, 16)}})
	d2 := make([]byte, BLCKSIZE)
	copy(d2, "T2_data_at_block_200_orphan")
	log := append(append(append(append(t1, d1...), c1), t2...), d2...)

	n := len(log) / BLCKSIZE
	fmt.Printf("log contains %d blocks (3 from T1 + 2 from T2)\n", n)
	var replayed []uint32
	i := 0
	for i < n {
		m, t, s := parseHeader(log[i*BLCKSIZE:])
		if m != JBD2Magic {
			i++
			continue
		}
		if t == DescBlock {
			dataStart := i + 1
			j := dataStart
			foundCommit := false
			for j < n {
				m2, t2_, s2 := parseHeader(log[j*BLCKSIZE:])
				if m2 != JBD2Magic {
					j++
					continue
				}
				if t2_ == DescBlock || t2_ == RevokeBlock {
					break
				}
				if t2_ == CommitBlock && s2 == s {
					foundCommit = true
					break
				}
				j++
			}
			if foundCommit {
				fmt.Printf("  ✓ replay T#%d (data blocks %d..%d)\n", s, dataStart, j-1)
				replayed = append(replayed, s)
				i = j + 1
			} else {
				fmt.Printf("  ✗ drop T#%d (no commit block found)\n", s)
				if j <= i {
					i++
				} else {
					i = j
				}
			}
		} else {
			i++
		}
	}
	fmt.Printf("total replayed transactions: %v (expect [10])\n", replayed)
	if len(replayed) != 1 || replayed[0] != 10 {
		panic(fmt.Sprintf("replay mismatch: got %v", replayed))
	}
}

func demo3Endian() {
	fmt.Println("\n=== demo 3: JBD2 big-endian vs ext4 little-endian ===")
	v := uint32(42)
	be := make([]byte, 4)
	binary.BigEndian.PutUint32(be, v)
	le := make([]byte, 4)
	binary.LittleEndian.PutUint32(le, v)
	fmt.Printf("value 42 → JBD2 big-endian   : % x\n", be)
	fmt.Printf("value 42 → ext4 little-endian: % x\n", le)
	fmt.Println("  → 解析 real ext4 journal 必须用 be32_to_cpu()/cpu_to_be32()")
}

func demo4Escape() {
	fmt.Println("\n=== demo 4: JBD2_FLAG_ESCAPE — data 前 4 字节 == magic 时 ===")
	block := make([]byte, BLCKSIZE)
	binary.BigEndian.PutUint32(block[0:], JBD2Magic)
	copy(block[4:], "rest of payload...")

	// 写入时:检测前 4B == magic → 清零
	onDisk := make([]byte, BLCKSIZE)
	copy(onDisk, block)
	needsEscape := be32u(onDisk, 0) == JBD2Magic
	if needsEscape {
		for k := 0; k < 4; k++ {
			onDisk[k] = 0
		}
	}

	// replay 时:还原
	recovered := make([]byte, BLCKSIZE)
	copy(recovered, onDisk)
	if needsEscape {
		binary.BigEndian.PutUint32(recovered[0:], JBD2Magic)
	}

	fmt.Printf("original block[0..7] : % x\n", block[:8])
	fmt.Printf("on_disk[0..7]        : % x  (前 4B 清零)\n", onDisk[:8])
	fmt.Printf("recovered[0..7]      : % x  (前 4B 还原)\n", recovered[:8])
	for i := 0; i < BLCKSIZE; i++ {
		if block[i] != recovered[i] {
			panic(fmt.Sprintf("ESCAPE round-trip failed at %d: %02x vs %02x",
				i, block[i], recovered[i]))
		}
	}
}

func main() {
	demo1WriteTransaction()
	demo2Replay()
	demo3Endian()
	demo4Escape()
	fmt.Println("\nall 4 demos done")
}