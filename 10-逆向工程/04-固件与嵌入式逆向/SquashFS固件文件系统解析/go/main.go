package main

import (
	"fmt"

	"sqfs"
)

func main() {
	sb := &sqfs.Superblock{
		Magic:       sqfs.Magic,
		Inodes:      430,
		BlockSize:   131072,
		Fragments:   64,
		Compression: sqfs.Xz,
		BlockLog:    17,
		Flags:       1 << sqfs.FlagNoFrag,
		NoIds:       1,
		Major:       4,
		RootInode:   sqfs.MKINODE(1, 0x10),
		BytesUsed:   0x3A0000,
	}
	raw := sb.Pack()
	p, _ := sqfs.ParseSuperblock(raw, 0)
	fmt.Printf("magic=%#x version=%d.%d compress=%d block=%d\n",
		p.Magic, p.Major, p.Minor, p.Compression, p.BlockSize)
	fmt.Printf("root=<blk %d, off %d> fragments=%d\n",
		sqfs.InodeBlk(p.RootInode), sqfs.InodeOffset(p.RootInode), p.Fragments)
	fmt.Printf("compressed(0x0003)=%v size=%d  size(0x8000)=%d\n",
		sqfs.Compressed(0x0003), sqfs.CompressedSize(0x0003), sqfs.CompressedSize(0x8000))
	fmt.Printf("dir_count(255)=%d ok=%v  index_blocks=%d\n",
		sqfs.DirCountOf(255), sqfs.DirCountOK(255), sqfs.FragmentIndexes(p.Fragments))
	fmt.Printf("datablock_count(131073,17,false)=%d with_frag=%d\n",
		sqfs.DataBlockCount(131073, 17, false), sqfs.DataBlockCount(131073, 17, true))
}
