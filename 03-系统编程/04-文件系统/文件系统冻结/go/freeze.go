// 文件系统冻结（freeze_super / FSFREEZE）的 Go 侧镜像。
//
// 来源同 Python 侧：fs/super.c 的分级状态机、may_freeze / may_unfreeze、
// freeze_inc/dec、thaw_super_locked；fsfreeze(8)
package main

import "fmt"

const (
	sbUnfrozen         = 0
	sbFreezeWrite      = 1
	sbFreezePagefault  = 2
	sbFreezeFS         = 3
	sbFreezeComplete   = 4
	sbFreezeLevels     = 3

	freezeHolderKernel    = 1
	freezeHolderUserspace = 2
	freezeMayNest         = 4
	freezeExcl            = 8
	freezeHolders         = freezeHolderKernel | freezeHolderUserspace
	freezeFlags           = freezeHolders | freezeMayNest | freezeExcl

	eBusy  = -16
	eInval = -22
)

var frozenName = map[int]string{
	sbUnfrozen: "SB_UNFROZEN", sbFreezeWrite: "SB_FREEZE_WRITE",
	sbFreezePagefault: "SB_FREEZE_PAGEFAULT", sbFreezeFS: "SB_FREEZE_FS",
	sbFreezeComplete: "SB_FREEZE_COMPLETE",
}

var sbWritersName = [sbFreezeLevels]string{
	"sb_writers", "sb_pagefaults", "sb_internal",
}

func countBits(x int) int {
	n := 0
	for x != 0 {
		n += x & 1
		x >>= 1
	}
	return n
}

// SuperBlock 是 struct super_block 里与冻结相关的状态。
type SuperBlock struct {
	Frozen             int
	FreezeKcount       int
	FreezeUcount       int
	FreezeOwner        string
	Rdonly             bool
	Active             [sbFreezeLevels]int
	Synced             int
	FreezeFsCalled     int
	UnfreezeFsCalled   int
}

// MayFreeze 对应 fs/super.c 的 may_freeze。
func MayFreeze(sb *SuperBlock, who int, owner string) bool {
	if who&^freezeFlags != 0 {
		return false
	}
	if countBits(who&freezeHolders) > 1 {
		return false
	}
	if who&freezeExcl != 0 {
		if who&freezeHolderKernel == 0 {
			return false
		}
		if who&^(freezeExcl|freezeHolderKernel) != 0 {
			return false
		}
		if owner == "" {
			return false
		}
		if sb.FreezeOwner != "" {
			return false
		}
		if sb.FreezeKcount+sb.FreezeUcount > 0 {
			sb.FreezeOwner = owner
		}
		return true
	}
	if who&freezeHolderKernel != 0 {
		return who&freezeMayNest != 0 || sb.FreezeKcount == 0
	}
	if who&freezeHolderUserspace != 0 {
		return who&freezeMayNest != 0 || sb.FreezeUcount == 0
	}
	return false
}

// MayUnfreeze 对应 may_unfreeze：与 may_freeze 不是同一套规则。
func MayUnfreeze(sb *SuperBlock, who int, owner string) bool {
	if who&^freezeFlags != 0 {
		return false
	}
	if countBits(who&freezeHolders) > 1 {
		return false
	}
	if who&freezeExcl != 0 {
		if who&freezeHolderKernel == 0 {
			return false
		}
		if who&^(freezeExcl|freezeHolderKernel) != 0 {
			return false
		}
		if who&freezeHolderKernel != 0 && sb.FreezeKcount == 0 {
			return false
		}
		if sb.FreezeOwner == "" || sb.FreezeOwner != owner {
			return false
		}
		if sb.FreezeKcount+sb.FreezeUcount > 1 {
			sb.FreezeOwner = ""
		}
		return true
	}
	if who&freezeHolderKernel != 0 {
		if sb.FreezeKcount == 1 && sb.FreezeOwner != "" {
			return false
		}
		return sb.FreezeKcount > 0
	}
	if who&freezeHolderUserspace != 0 {
		return sb.FreezeUcount > 0
	}
	return false
}

func freezeInc(sb *SuperBlock, who int) int {
	if who&freezeHolderKernel != 0 {
		sb.FreezeKcount++
	}
	if who&freezeHolderUserspace != 0 {
		sb.FreezeUcount++
	}
	return sb.FreezeKcount + sb.FreezeUcount
}

func freezeDec(sb *SuperBlock, who int) int {
	if who&freezeHolderKernel != 0 && sb.FreezeKcount > 0 {
		sb.FreezeKcount--
	}
	if who&freezeHolderUserspace != 0 && sb.FreezeUcount > 0 {
		sb.FreezeUcount--
	}
	return sb.FreezeKcount + sb.FreezeUcount
}

func waitWrite(sb *SuperBlock, level int, wait bool) bool {
	idx := level - 1
	if sb.Active[idx] != 0 {
		if !wait {
			return false
		}
		sb.Active[idx] = 0
	}
	return true
}

// FreezeSuper 推进分级状态机，返回 0 或负 errno。
func FreezeSuper(sb *SuperBlock, who int, owner string, wait bool) int {
	if sb.Frozen == sbFreezeComplete {
		if MayFreeze(sb, who, owner) {
			freezeInc(sb, who)
			return 0
		}
		return eBusy
	}
	if sb.Frozen != sbUnfrozen {
		return eBusy
	}
	if sb.Rdonly {
		sb.FreezeOwner = owner
		sb.Frozen = sbFreezeComplete
		return 0
	}
	sb.Frozen = sbFreezeWrite
	if !waitWrite(sb, sbFreezeWrite, wait) {
		return eBusy
	}
	sb.Frozen = sbFreezePagefault
	if !waitWrite(sb, sbFreezePagefault, wait) {
		return eBusy
	}
	sb.Synced++
	sb.Frozen = sbFreezeFS
	if !waitWrite(sb, sbFreezeFS, wait) {
		return eBusy
	}
	sb.FreezeFsCalled++
	freezeInc(sb, who)
	sb.FreezeOwner = owner
	sb.Frozen = sbFreezeComplete
	return 0
}

// ThawSuper 解冻；计数归零才真正解冻。
func ThawSuper(sb *SuperBlock, who int, owner string) int {
	if sb.Frozen != sbFreezeComplete {
		return eInval
	}
	if !MayUnfreeze(sb, who, owner) {
		return eBusy
	}
	if freezeDec(sb, who) != 0 {
		return 0
	}
	sb.UnfreezeFsCalled++
	sb.Frozen = sbUnfrozen
	sb.FreezeOwner = ""
	return 0
}

func BlocksWrite(f int) bool     { return f >= sbFreezeWrite }
func BlocksPagefault(f int) bool { return f >= sbFreezePagefault }
func BlocksInternal(f int) bool  { return f >= sbFreezeFS }

// Snapshot 是一致性快照模型：必须在完全冻结之后拍。
type Snapshot struct {
	SB             *SuperBlock
	TakenAtFrozen  int
	Consistent     bool
}

func (s *Snapshot) Take() bool {
	s.TakenAtFrozen = s.SB.Frozen
	s.Consistent = s.SB.Frozen == sbFreezeComplete || s.SB.Rdonly
	return s.Consistent
}

func (s *Snapshot) NeedsReplay() bool { return !s.Consistent }

func main() {
	fmt.Printf("== 分级（%v） ==\n", sbWritersName)
	for _, f := range []int{sbUnfrozen, sbFreezeWrite, sbFreezePagefault,
		sbFreezeFS, sbFreezeComplete} {
		fmt.Printf("  %-20s 挡新写=%-5v 挡页错误=%-5v 挡内部写入=%v\n",
			frozenName[f], BlocksWrite(f), BlocksPagefault(f), BlocksInternal(f))
	}

	fmt.Println("== 冻结全过程 ==")
	sb := &SuperBlock{}
	FreezeSuper(sb, freezeHolderUserspace, "", true)
	fmt.Printf("  freeze → %s（sync=%d, freeze_fs=%d）\n",
		frozenName[sb.Frozen], sb.Synced, sb.FreezeFsCalled)

	fmt.Println("== 半冻结 ==")
	sb2 := &SuperBlock{}
	sb2.Active[0] = 1
	r := FreezeSuper(sb2, freezeHolderUserspace, "", false)
	fmt.Printf("  有活跃写者 → 返回 %d，停在 %s\n", r, frozenName[sb2.Frozen])

	fmt.Println("== 只读 fs ==")
	sb3 := &SuperBlock{Rdonly: true}
	FreezeSuper(sb3, freezeHolderUserspace, "", true)
	fmt.Printf("  冻结 → %s（sync=%d, freeze_fs=%d）\n",
		frozenName[sb3.Frozen], sb3.Synced, sb3.FreezeFsCalled)

	fmt.Println("== 嵌套冻结 ==")
	sb4 := &SuperBlock{}
	FreezeSuper(sb4, freezeHolderKernel|freezeMayNest, "", true)
	FreezeSuper(sb4, freezeHolderKernel|freezeMayNest, "", true)
	fmt.Printf("  冻两次 → kcount=%d %s\n", sb4.FreezeKcount, frozenName[sb4.Frozen])
	ThawSuper(sb4, freezeHolderKernel, "")
	fmt.Printf("  thaw 一次 → kcount=%d %s\n", sb4.FreezeKcount, frozenName[sb4.Frozen])
	ThawSuper(sb4, freezeHolderKernel, "")
	fmt.Printf("  再 thaw → %s，unfreeze_fs 调了 %d 次\n",
		frozenName[sb4.Frozen], sb4.UnfreezeFsCalled)

	fmt.Println("== 快照一致性 ==")
	sb5 := &SuperBlock{}
	early := &Snapshot{SB: sb5}
	fmt.Printf("  未冻结就拍 → consistent=%v，需 replay=%v\n",
		early.Take(), early.NeedsReplay())
	FreezeSuper(sb5, freezeHolderUserspace, "", true)
	ok := &Snapshot{SB: sb5}
	fmt.Printf("  冻结后拍   → consistent=%v，需 replay=%v\n",
		ok.Take(), ok.NeedsReplay())
}
