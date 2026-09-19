// cow_crc.go — btrfs send stream 的 CRC32C 校验
//
// btrfs 文档「Send/receive」原文：每条命令由 CRC32C 校验和，
// **初值为 0 且不取反**（with 0 as the initial value and no inversion）。
// 这与常见的 CRC-32C 形态（初值 0xFFFFFFFF、结果取反）不同，混用即校验失败。

package main

import "fmt"

const poly = 0x82F63B78 // CRC-32C (Castagnoli) 反射形式

var table = func() []uint32 {
	t := make([]uint32, 256)
	for i := 0; i < 256; i++ {
		c := uint32(i)
		for j := 0; j < 8; j++ {
			if c&1 != 0 {
				c = (c >> 1) ^ poly
			} else {
				c >>= 1
			}
		}
		t[i] = c
	}
	return t
}()

// CRC32C 把 init / xorout 做成参数，好对照两种形态。
func CRC32C(data []byte, init uint32, xorout bool) uint32 {
	crc := init
	for _, b := range data {
		crc = table[(crc^b)&0xFF] ^ (crc >> 8)
	}
	if xorout {
		return crc ^ 0xFFFFFFFF
	}
	return crc
}

// BtrfsCsum 是 send stream 用的形态：init=0，不取反。
func BtrfsCsum(data []byte) uint32 {
	return CRC32C(data, 0, false)
}

type command struct {
	Cmd     string
	Payload []byte
	Csum    uint32
}

func demoCRC() {
	data := []byte("btrfs-send-stream")
	std := CRC32C(data, 0xFFFFFFFF, true)
	bt := BtrfsCsum(data)
	fmt.Printf("CRC32C 标准形态 = %#010x, btrfs 形态 = %#010x, 相同? %v\n",
		std, bt, std == bt)

	stream := []command{
		{"create", []byte("a.txt"), 0},
		{"write", []byte("a.txt:3"), 0},
		{"set_readonly", []byte{}, 0},
	}
	for i := range stream {
		stream[i].Csum = BtrfsCsum(stream[i].Payload)
	}
	bad := 0
	for _, c := range stream {
		if BtrfsCsum(c.Payload) != c.Csum {
			bad++
		}
	}
	fmt.Printf("send stream %d 条命令, 校验失败 %d 条\n", len(stream), bad)

	stream[1].Csum ^= 1
	bad = 0
	for _, c := range stream {
		if BtrfsCsum(c.Payload) != c.Csum {
			bad++
		}
	}
	fmt.Printf("篡改 1 个 bit 后, 校验失败 %d 条\n", bad)
}
