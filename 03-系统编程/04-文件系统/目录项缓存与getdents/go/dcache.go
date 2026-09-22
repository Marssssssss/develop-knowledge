// dcache 与 getdents64 的 Go 侧镜像。
//
// 来源同 Python 侧：include/linux/dcache.h、fs/readdir.c、getdents(2)、
// glibc 的 dirent/dirent.h（DT_* 数值）
package main

import (
	"encoding/binary"
	"fmt"
)

const (
	dcacheDisconnected      = 1 << 5
	dcacheReferenced        = 1 << 6
	dcacheDontcache         = 1 << 7
	dcacheCantMount         = 1 << 8
	dcacheShrinkList        = 1 << 10
	dcacheDentryKilled      = 1 << 14
	dcacheMounted           = 1 << 15
	dcacheNeedAutomount     = 1 << 16
	dcacheManageTransit     = 1 << 17
	dcacheLRUList           = 1 << 18
	dcacheEntryType         = 7 << 19
	dcacheMissType          = 0 << 19
	dcacheWhiteoutType      = 1 << 19
	dcacheDirectoryType     = 2 << 19
	dcacheAutodirType       = 3 << 19
	dcacheRegularType       = 4 << 19
	dcacheSpecialType       = 5 << 19
	dcacheSymlinkType       = 6 << 19
	dcacheNokeyName         = 1 << 22
	dcacheOpReal            = 1 << 23
	dcacheParLookup         = 1 << 24
	dcachePersistent        = 1 << 27
	dcacheManagedDentry     = dcacheMounted | dcacheNeedAutomount | dcacheManageTransit

	dtUnknown = 0
	dtFIFO    = 1
	dtChr     = 2
	dtDir     = 4
	dtBlk     = 6
	dtReg     = 8
	dtLnk     = 10
	dtSock    = 12
	dtWht     = 14

	dirent64Header = 19 // 8+8+2+1
	alignTo        = 8
	eInval         = -22
)

var typeName = map[int]string{
	dcacheMissType: "MISS", dcacheWhiteoutType: "WHITEOUT",
	dcacheDirectoryType: "DIRECTORY", dcacheAutodirType: "AUTODIR",
	dcacheRegularType: "REGULAR", dcacheSpecialType: "SPECIAL",
	dcacheSymlinkType: "SYMLINK",
}

var dtName = map[int]string{
	dtUnknown: "DT_UNKNOWN", dtFIFO: "DT_FIFO", dtChr: "DT_CHR",
	dtDir: "DT_DIR", dtBlk: "DT_BLK", dtReg: "DT_REG", dtLnk: "DT_LNK",
	dtSock: "DT_SOCK", dtWht: "DT_WHT",
}

var dcacheToDT = map[int]int{
	dcacheMissType: dtUnknown, dcacheWhiteoutType: dtWht,
	dcacheDirectoryType: dtDir, dcacheAutodirType: dtDir,
	dcacheRegularType: dtReg, dcacheSpecialType: dtUnknown,
	dcacheSymlinkType: dtLnk,
}

// DentryType 取出 d_flags 里 19..21 位的类型字段。
func DentryType(flags int) int { return flags & dcacheEntryType }

// DIsNegative 表示 d_inode 为 NULL 的 negative dentry。
func DIsNegative(flags int) bool { return DentryType(flags) == dcacheMissType }

func DIsWhiteout(flags int) bool  { return DentryType(flags) == dcacheWhiteoutType }
func DIsDirectory(flags int) bool { return DentryType(flags) == dcacheDirectoryType }
func DIsAutodir(flags int) bool   { return DentryType(flags) == dcacheAutodirType }
func DIsReg(flags int) bool       { return DentryType(flags) == dcacheRegularType }
func DIsSpecial(flags int) bool   { return DentryType(flags) == dcacheSpecialType }
func DIsSymlink(flags int) bool   { return DentryType(flags) == dcacheSymlinkType }
func DCanLookup(flags int) bool   { return DIsDirectory(flags) || DIsAutodir(flags) }

// SetType 只改类型位，其余标志位保持原样。
func SetType(flags, t int) int { return (flags & ^dcacheEntryType) | t }

// Dentry 是 struct dentry 的模型。d_alloc 出来的初始 refcount 是 1。
type Dentry struct {
	Name     string
	Flags    int
	Refcount int
	Inode    int
	HasInode bool
	Killed   bool
}

// NewDentry 建一个 dentry（初值 refcount=1，即 in-use）。
func NewDentry(name string, typ int) *Dentry {
	return &Dentry{Name: name, Flags: typ, Refcount: 1}
}
