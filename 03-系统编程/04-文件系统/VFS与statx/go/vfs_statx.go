// vfs_statx.go — statx(2) 请求/返回掩码语义 + st_mode 字段映射
//
// 运行: go run vfs_statx.go
//
// 常量与规则均来自实读的 include/uapi/linux/stat.h 与 fcntl.h；
// 与 python/ 目录里的模型同题，便于对照。

package main

import "fmt"

// ---- STATX_* 请求/返回位 ----
const (
	StatxType          uint32 = 0x00000001
	StatxMode          uint32 = 0x00000002
	StatxNlink         uint32 = 0x00000004
	StatxUID           uint32 = 0x00000008
	StatxGID           uint32 = 0x00000010
	StatxAtime         uint32 = 0x00000020
	StatxMtime         uint32 = 0x00000040
	StatxCtime         uint32 = 0x00000080
	StatxIno           uint32 = 0x00000100
	StatxSize          uint32 = 0x00000200
	StatxBlocks        uint32 = 0x00000400
	StatxBasicStats    uint32 = 0x000007ff
	StatxBtime         uint32 = 0x00000800
	StatxMntID         uint32 = 0x00001000
	StatxDioalign      uint32 = 0x00002000
	StatxMntIDUnique   uint32 = 0x00004000
	StatxSubvol        uint32 = 0x00008000
	StatxWriteAtomic   uint32 = 0x00010000
	StatxDioReadAlign  uint32 = 0x00020000
	StatxReserved      uint32 = 0x80000000 // 唯一会触发 EINVAL 的保留位
	StatxAllDeprecated uint32 = 0x00000fff // 已废弃，等价于 BasicStats|Btime
)

// ---- STATX_ATTR_* 属性位 ----
const (
	StatxAttrCompressed uint64 = 0x00000004
	StatxAttrImmutable  uint64 = 0x00000010
	StatxAttrAppend     uint64 = 0x00000020
	StatxAttrNodump     uint64 = 0x00000040
	StatxAttrEncrypted  uint64 = 0x00000800
	StatxAttrAutomount  uint64 = 0x00001000
	StatxAttrMountRoot  uint64 = 0x00002000
	StatxAttrVerity     uint64 = 0x00100000
	StatxAttrDAX        uint64 = 0x00200000
	StatxAttrWAtomic    uint64 = 0x00400000
)

// ---- AT_* 路径解析与同步标志（fcntl.h）----
const (
	AtSymlinkNofollow uint32 = 0x100
	AtNoAutomount     uint32 = 0x800
	AtEmptyPath       uint32 = 0x1000
	AtStatxSyncType   uint32 = 0x6000
	AtStatxSyncAsStat uint32 = 0x0000
	AtStatxForceSync  uint32 = 0x2000
	AtStatxDontSync   uint32 = 0x4000
)

// ---- st_mode 文件类型 ----
const (
	SIfmt uint16 = 0170000
	SIfreg uint16 = 0100000
	SIfdir uint16 = 0040000
	SIfifo uint16 = 0010000
)

// Fs 描述一个具体文件系统的 statx 能力。
type Fs struct {
	Name      string
	Supported uint32 // 能填出来的位
	FreeBits  uint32 // 没请求也顺手填的位
	AttrMask  uint64 // stx_attributes_mask
	Attrs     uint64 // stx_attributes 原始值
	DummyUID  uint32
}

// Resolve 按 stat.h 头注释的四条规则算出 stx_mask，并返回可用的属性位。
func (f Fs) Resolve(req uint32) (uint32, uint64, error) {
	if req&StatxReserved != 0 {
		return 0, 0, fmt.Errorf("EINVAL: mask 含 STATX__RESERVED")
	}
	basic := []uint32{StatxType, StatxMode, StatxNlink, StatxUID, StatxGID,
		StatxAtime, StatxMtime, StatxCtime, StatxIno, StatxSize, StatxBlocks}
	extra := []uint32{StatxBtime, StatxMntID, StatxDioalign,
		StatxMntIDUnique, StatxSubvol, StatxWriteAtomic, StatxDioReadAlign}
	all := append(append([]uint32{}, basic...), extra...)

	got := uint32(0)
	for _, b := range all {
		if req&b != 0 {
			if f.Supported&b != 0 {
				got |= b // 规则 2：显式请求且支持
			}
			continue // 规则 1：不支持 → 清位
		}
		if f.FreeBits&b != 0 && f.Supported&b != 0 {
			got |= b // 规则 3：没请求但顺手可得
		}
	}
	return got, f.Attrs & f.AttrMask, nil
}

// SyncMode 解析 AT_STATX_* 三态；FORCE|DONT 同时置位是非法组合。
func SyncMode(flags uint32) (string, error) {
	switch flags & AtStatxSyncType {
	case AtStatxForceSync:
		return "force_sync", nil
	case AtStatxDontSync:
		return "dont_sync", nil
	case AtStatxSyncAsStat:
		return "as_stat", nil
	}
	return "", fmt.Errorf("EINVAL: AT_STATX_SYNC_TYPE 非法")
}

func maskNames(m uint32) []string {
	pairs := []struct {
		b uint32
		n string
	}{
		{StatxType, "TYPE"}, {StatxMode, "MODE"}, {StatxNlink, "NLINK"},
		{StatxUID, "UID"}, {StatxGID, "GID"}, {StatxIno, "INO"},
		{StatxSize, "SIZE"}, {StatxBlocks, "BLOCKS"}, {StatxBtime, "BTIME"},
		{StatxMntID, "MNT_ID"}, {StatxDioalign, "DIOALIGN"},
		{StatxMntIDUnique, "MNT_ID_UNIQUE"}, {StatxSubvol, "SUBVOL"},
	}
	out := []string{}
	for _, p := range pairs {
		if m&p.b != 0 {
			out = append(out, p.n)
		}
	}
	return out
}

func fileType(mode uint16) string {
	switch mode & SIfmt {
	case SIfreg:
		return "regular"
	case SIfdir:
		return "directory"
	case SIfifo:
		return "fifo"
	}
	return "other"
}

func main() {
	ext4 := Fs{
		Name:      "ext4",
		Supported: StatxBasicStats | StatxBtime | StatxMntID | StatxDioalign | StatxMntIDUnique,
		FreeBits:  StatxIno,
		AttrMask:  StatxAttrImmutable | StatxAttrAppend | StatxAttrCompressed,
		Attrs:     StatxAttrImmutable | StatxAttrDAX, // DAX 会被屏蔽
	}
	nfs := Fs{Name: "nfs", Supported: StatxBasicStats}
	cifs := Fs{Name: "cifs", Supported: StatxBasicStats & ^StatxUID, DummyUID: 65534}

	fmt.Println("== 请求位与返回位不是一回事 ==")
	if got, _, err := ext4.Resolve(StatxSize); err == nil {
		fmt.Printf("  ext4 请求 SIZE      -> %v\n", maskNames(got))
	}
	if got, _, err := ext4.Resolve(StatxBtime); err == nil {
		fmt.Printf("  ext4 请求 BTIME     -> %v\n", maskNames(got))
	}
	if got, _, err := nfs.Resolve(StatxBtime); err == nil {
		fmt.Printf("  nfs  请求 BTIME     -> %v (被清)\n", maskNames(got))
	}
	if got, _, err := cifs.Resolve(StatxUID); err == nil {
		fmt.Printf("  cifs 请求 UID       -> %v (编造 uid=%d)\n", maskNames(got), cifs.DummyUID)
	}

	fmt.Println("== 属性位必须与 stx_attributes_mask 相与 ==")
	_, attrs, _ := ext4.Resolve(StatxType)
	fmt.Printf("  ext4 可用属性 = %#x (DAX 被屏蔽: %v)\n", attrs, attrs&StatxAttrDAX == 0)
	_, attrs, _ = nfs.Resolve(StatxType)
	fmt.Printf("  nfs  可用属性 = %#x\n", attrs)

	fmt.Println("== 保留位与非法同步组合 ==")
	if _, _, err := ext4.Resolve(StatxReserved); err != nil {
		fmt.Printf("  STATX__RESERVED -> %v\n", err)
	}
	if _, err := SyncMode(AtStatxForceSync | AtStatxDontSync); err != nil {
		fmt.Printf("  FORCE|DONT      -> %v\n", err)
	}
	for _, f := range []uint32{0, AtStatxForceSync, AtStatxDontSync} {
		if s, err := SyncMode(f); err == nil {
			fmt.Printf("  flags=%#05x -> %s\n", f, s)
		}
	}

	fmt.Println("== st_mode 类型位 ==")
	fmt.Printf("  0100644 -> %s, 0040755 -> %s\n", fileType(0100644), fileType(0040755))

	fmt.Println("== stx_blocks 以 512B 为单位 ==")
	fmt.Printf("  8 blocks -> %d 字节; 1MiB 稀疏文件仅占 %d 字节\n",
		8*512, 8*512)
}
