package main

import (
	"fmt"

	"jffs2ubi"
)

func main() {
	n := &jffs2ubi.RawNode{Magic: jffs2ubi.MagicBitmask,
		Nodetype: jffs2ubi.NodetypeInode, Totlen: 100}
	jffs2ubi.SealCommon(n)
	raw := n.Common()
	p, _ := jffs2ubi.ParseCommon(raw, 0)
	fmt.Printf("jffs2 magic=%#06x type=%#06x totlen=%d hdr_crc_ok=%v incompat=%v\n",
		p.Magic, p.Nodetype, p.Totlen, p.CheckHdrCRC(), jffs2ubi.IsIncompat(p.Nodetype))

	ec := jffs2ubi.PackEC(&jffs2ubi.ECHeader{
		Magic: jffs2ubi.UBIEcHdrMagic, Version: jffs2ubi.UBIVersion,
		Ec: 7, VidHdrOffset: 2048, DataOffset: 4096, ImageSeq: 0x11223344})
	fmt.Printf("ubi ec magic=%#010x size=%d crc_ok=%v\n",
		uint32(ec[0])<<24|uint32(ec[1])<<16|uint32(ec[2])<<8|uint32(ec[3]),
		len(ec), jffs2ubi.CheckECCRC(ec))

	vid := jffs2ubi.PackVID(&jffs2ubi.VIDHeader{
		Magic: jffs2ubi.UBIVidHdrMagic, Version: jffs2ubi.UBIVersion,
		VolType: jffs2ubi.UBIVidDynamic, VolID: 0, Lnum: 3, Sqnum: 11})
	fmt.Printf("ubi vid size=%d crc_ok=%v layout_id=%#x\n",
		len(vid), jffs2ubi.CheckVIDCRC(vid), jffs2ubi.LayoutVolumeID)

	old := &jffs2ubi.VIDHeader{Sqnum: 10}
	nw := &jffs2ubi.VIDHeader{Sqnum: 20, CopyFlag: 1}
	fmt.Printf("copy+crc_bad -> old=%v  copy+crc_ok -> new=%v\n",
		jffs2ubi.PickPEB(old, nw, false) == old,
		jffs2ubi.PickPEB(old, nw, true) == nw)
}
