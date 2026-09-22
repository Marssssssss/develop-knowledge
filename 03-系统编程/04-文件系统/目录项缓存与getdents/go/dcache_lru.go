package main

import (
	"encoding/binary"
	"fmt"
)

// DCache 是哈希 + LRU 的最小模型。
type DCache struct {
	Table map[string]*Dentry
	LRU   []*Dentry
}

// NewDCache 建一个空 dcache。
func NewDCache() *DCache { return &DCache{Table: map[string]*Dentry{}} }

// Insert 把 dentry 放进哈希表。
func (c *DCache) Insert(d *Dentry) {
	c.Table[d.Name] = d
}

// Lookup 按名字查。
func (c *DCache) Lookup(name string) *Dentry { return c.Table[name] }

// Dget 增加引用，若在 LRU 上则摘下。
func (c *DCache) Dget(d *Dentry) {
	d.Refcount++
	if d.Refcount == 1 {
		c.removeLRU(d)
	}
}

// Dput 减少引用，归零则挂到 LRU 尾部。
func (c *DCache) Dput(d *Dentry) {
	d.Refcount--
	if d.Refcount == 0 && !d.Killed && !c.inLRU(d) {
		c.LRU = append(c.LRU, d)
	}
}

func (c *DCache) inLRU(d *Dentry) bool {
	for _, x := range c.LRU {
		if x == d {
			return true
		}
	}
	return false
}

func (c *DCache) removeLRU(d *Dentry) {
	for i, x := range c.LRU {
		if x == d {
			c.LRU = append(c.LRU[:i], c.LRU[i+1:]...)
			return
		}
	}
}

// TouchLRU 置 REFERENCED 并移到 LRU 尾部。
func (c *DCache) TouchLRU(d *Dentry) {
	d.Flags |= dcacheReferenced
	c.removeLRU(d)
	c.LRU = append(c.LRU, d)
}

// ShrinkOne 回收一个 unused dentry；带 REFERENCED 的先清标志再给第二次机会。
func (c *DCache) ShrinkOne() string {
	for len(c.LRU) > 0 {
		d := c.LRU[0]
		c.LRU = c.LRU[1:]
		if d.Refcount != 0 {
			continue
		}
		if d.Flags&dcacheReferenced != 0 {
			d.Flags &= ^dcacheReferenced
			c.LRU = append(c.LRU, d)
			continue
		}
		d.Killed = true
		d.Flags |= dcacheDentryKilled
		if c.Table[d.Name] == d {
			delete(c.Table, d.Name)
		}
		return d.Name
	}
	return ""
}

// NegativeLookup 查不到也建 dentry（negative），避免反复下探文件系统。
func (c *DCache) NegativeLookup(name string) *Dentry {
	d := NewDentry(name, dcacheMissType)
	d.HasInode = false
	c.Insert(d)
	return d
}

// Dirent64Size 返回一条 linux_dirent64 占多少字节（含 NUL 与 8 字节对齐）。
func Dirent64Size(name string) int {
	raw := dirent64Header + len(name) + 1
	return (raw + alignTo - 1) / alignTo * alignTo
}

// EmitGetdents 模拟一次 getdents64：装不下就停，不截断。
// 只要发出过至少一条就返回写入字节数；一条都装不下才返回 -EINVAL。
func EmitGetdents(names []string, bufferSize int) (int, int, int) {
	count := bufferSize
	n := 0
	for _, name := range names {
		size := Dirent64Size(name)
		if size > count {
			break
		}
		count -= size
		n++
	}
	if n == 0 {
		return 0, len(names), eInval
	}
	return bufferSize - count, len(names) - n, bufferSize - count
}

// Dirent64Record 打包一条 linux_dirent64。
func Dirent64Record(ino uint64, off uint64, name string, dtype int) []byte {
	size := Dirent64Size(name)
	b := make([]byte, size)
	binary.LittleEndian.PutUint64(b[0:], ino)
	binary.LittleEndian.PutUint64(b[8:], off)
	binary.LittleEndian.PutUint16(b[16:], uint16(size))
	b[18] = byte(dtype)
	copy(b[19:], name)
	return b
}

func main() {
	fmt.Println("== d_flags 的类型字段（3 位，19..21） ==")
	fmt.Printf("  DCACHE_ENTRY_TYPE = 0x%X\n", dcacheEntryType)
	for _, t := range []int{dcacheMissType, dcacheWhiteoutType, dcacheDirectoryType,
		dcacheAutodirType, dcacheRegularType, dcacheSpecialType, dcacheSymlinkType} {
		fmt.Printf("  %-10s → 值 %d, d_type %s\n",
			typeName[t], t>>19, dtName[dcacheToDT[t]])
	}

	fmt.Println("== dcache 生命周期 ==")
	c := NewDCache()
	d := NewDentry("f", dcacheRegularType)
	c.Insert(d)
	fmt.Printf("  d_alloc   : ref=%d lru=%v\n", d.Refcount, c.inLRU(d))
	c.Dput(d)
	fmt.Printf("  dput      : ref=%d lru=%v\n", d.Refcount, c.inLRU(d))
	c.Dget(d)
	fmt.Printf("  dget      : ref=%d lru=%v\n", d.Refcount, c.inLRU(d))
	neg := c.NegativeLookup("missing")
	fmt.Printf("  negative  : type=%s 有 inode=%v 在哈希=%v\n",
		typeName[DentryType(neg.Flags)], neg.HasInode, c.Lookup("missing") == neg)

	c2 := NewDCache()
	var freed []string
	for _, n := range []string{"x", "y", "z"} {
		dd := NewDentry(n, dcacheRegularType)
		c2.Insert(dd)
		c2.Dput(dd)
	}
	c2.TouchLRU(c2.LRU[0])
	for i := 0; i < 3; i++ {
		freed = append(freed, c2.ShrinkOne())
	}
	fmt.Printf("  收缩顺序   : %v\n", freed)

	fmt.Println("== struct linux_dirent64 ==")
	for _, n := range []string{"a", "abc", "abcdef", "hello.txt"} {
		fmt.Printf("  name=%-10s 原始 %-3d → d_reclen %d\n",
			n, dirent64Header+len(n)+1, Dirent64Size(n))
	}

	fmt.Println("== getdents64 装填与 EINVAL 时机 ==")
	names := []string{"one", "two", "three"}
	for _, bs := range []int{4096, Dirent64Size("one"), Dirent64Size("one") - 1, 0} {
		written, _, ret := EmitGetdents(names, bs)
		if ret == eInval {
			fmt.Printf("  buffer=%-5d → 写入 0 条，返回 EINVAL\n", bs)
		} else {
			fmt.Printf("  buffer=%-5d → 写入 %d 条，返回 %d 字节\n", bs, written, ret)
		}
	}

	fmt.Println("== 大目录遍历 ==")
	many := make([]string, 5000)
	for i := range many {
		many[i] = fmt.Sprintf("f%05d", i)
	}
	pos, calls, total := 0, 0, 0
	for pos < len(many) {
		written, _, ret := EmitGetdents(many[pos:], 4096)
		if ret == eInval {
			break
		}
		n := 0
		for _, nm := range many[pos:] {
			if written < Dirent64Size(nm) {
				break
			}
			written -= Dirent64Size(nm)
			n++
		}
		pos += n
		total += n
		calls++
	}
	fmt.Printf("  5000 条目 / 4KiB 缓冲 → %d 次调用，收齐 %d 条\n", calls, total)
}
