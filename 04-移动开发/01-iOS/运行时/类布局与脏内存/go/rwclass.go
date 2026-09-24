package main

import "errors"

// ---- class_ro_t / class_rw_ext_t / class_rw_t ----

type ClassRo struct {
	FlagsField     uint32
	Name           string
	BaseMethods    *ListOrList
	BaseProperties *ListOrList
	BaseProtocols  *ListOrList
}

type ClassRwExt struct {
	Ro            *ClassRo
	Methods       *ListArray
	Properties    *ListArray
	Protocols     *ListArray
	DemangledName string
	Version       uint32
}

func newClassRwExt() *ClassRwExt {
	return &ClassRwExt{Methods: newListArray(), Properties: newListArray(),
		Protocols: newListArray()}
}

// RoOrRwExt 对应 objc::PointerUnion<const class_ro_t, class_rw_ext_t, ...>。
// 标签是最低位：class_ro_t 原样存，class_rw_ext_t 置位 1；取值时 auth 后 & ~1。
type RoOrRwExt struct {
	raw  uint64
	ptra PtrAuth
}

func (u RoOrRwExt) Tag() uint64 { return u.raw & 1 }

func (u RoOrRwExt) IsRo() bool  { return u.Tag() == 0 }
func (u RoOrRwExt) IsRwe() bool { return u.Tag() == 1 }
func (u RoOrRwExt) IsNull() bool {
	return u.raw == 0 // 整个字为零才算空
}

func (u RoOrRwExt) Addr() (uint64, error) {
	a, err := u.ptra.Auth(u.raw)
	if err != nil {
		return 0, err
	}
	return a & ^uint64(1), nil
}

func (u RoOrRwExt) GetRo() (*ClassRo, error) {
	if !u.IsRo() {
		return nil, errors.New("not a class_ro_t")
	}
	a, err := u.Addr()
	if err != nil {
		return nil, err
	}
	ro, ok := deref(a).(*ClassRo)
	if !ok {
		return nil, errors.New("ro pointer does not hold a class_ro_t")
	}
	return ro, nil
}

func (u RoOrRwExt) GetRwe() (*ClassRwExt, error) {
	if !u.IsRwe() {
		return nil, errors.New("not a class_rw_ext_t")
	}
	a, err := u.Addr()
	if err != nil {
		return nil, err
	}
	rwe, ok := deref(a).(*ClassRwExt)
	if !ok {
		return nil, errors.New("rwe pointer does not hold a class_rw_ext_t")
	}
	return rwe, nil
}

func (u RoOrRwExt) DynRwe() *ClassRwExt {
	rwe, err := u.GetRwe()
	if err != nil {
		return nil
	}
	return rwe
}

// Truthy 对应 operator bool()：两种类型各自 dyn_cast，任一非空即为真。
func (u RoOrRwExt) Truthy() bool {
	a, err := u.Addr()
	return err == nil && a != 0
}

type ClassRw struct {
	arch            Arch
	FlagsField      uint32
	Witness         uint16
	RoOrRwExtValue  uint64
	ptra            PtrAuth
	CasFailures     int
	CasAttempts     int
	FirstSubclass   *ClassRw
	NextSibling     *ClassRw
}

func newClassRw(arch Arch, ro *ClassRo, flags uint32, disc uint64) *ClassRw {
	rw := &ClassRw{arch: arch, FlagsField: flags, ptra: newPtrAuth(arch, disc)}
	rw.setRoOrRweRo(ro)
	return rw
}

func (rw *ClassRw) setRoOrRweRo(ro *ClassRo) {
	rw.RoOrRwExtValue = rw.ptra.Sign(alloc(ro))
}

func (rw *ClassRw) setRoOrRweRwe(rwe *ClassRwExt, ro *ClassRo) {
	// 先把 rwe->ro 写好，再用 release 屏障存指针：
	// 源码注释说这一步是为了让无锁读者能看到 rwe->ro 的初始化
	rwe.Ro = ro
	rw.RoOrRwExtValue = rw.ptra.Sign(alloc(rwe) | 1)
}

func (rw *ClassRw) GetRoOrRwe() RoOrRwExt {
	return RoOrRwExt{raw: rw.RoOrRwExtValue, ptra: rw.ptra}
}

func (rw *ClassRw) Ext() *ClassRwExt {
	return rw.GetRoOrRwe().DynRwe()
}

func (rw *ClassRw) Ro() (*ClassRo, error) {
	v := rw.GetRoOrRwe()
	if v.IsRwe() {
		rwe, err := v.GetRwe()
		if err != nil {
			return nil, err
		}
		return rwe.Ro, nil
	}
	return v.GetRo()
}

func (rw *ClassRw) SetRo(ro *ClassRo) error {
	v := rw.GetRoOrRwe()
	if v.IsRwe() {
		rwe, err := v.GetRwe()
		if err != nil {
			return err
		}
		rwe.Ro = ro
		return nil
	}
	rw.setRoOrRweRo(ro)
	return nil
}

// MethodAlternates 返回三种表示，任何时刻最多一个非 nil。
type MethodAlternates struct {
	Array        *ListArray
	List         *Item
	RelativeList []*Item
}

func (rw *ClassRw) MethodAlternates() (MethodAlternates, error) {
	v := rw.GetRoOrRwe()
	if v.IsRwe() {
		rwe, err := v.GetRwe()
		if err != nil {
			return MethodAlternates{}, err
		}
		return MethodAlternates{Array: rwe.Methods}, nil
	}
	ro, err := v.GetRo()
	if err != nil {
		return MethodAlternates{}, err
	}
	return MethodAlternates{List: ro.BaseMethods.DynList(),
		RelativeList: ro.BaseMethods.DynRel()}, nil
}

func (rw *ClassRw) Methods() ([]*Item, error) {
	a, err := rw.MethodAlternates()
	if err != nil {
		return nil, err
	}
	switch {
	case a.Array != nil:
		return a.Array.Lists(), nil
	case a.List != nil:
		return []*Item{a.List}, nil
	case a.RelativeList != nil:
		return a.RelativeList, nil
	}
	return nil, nil
}

func (rw *ClassRw) listOf(field string) ([]*Item, error) {
	v := rw.GetRoOrRwe()
	var base *ListOrList
	if v.IsRwe() {
		rwe, err := v.GetRwe()
		if err != nil {
			return nil, err
		}
		switch field {
		case "properties":
			return rwe.Properties.Lists(), nil
		default:
			return rwe.Protocols.Lists(), nil
		}
	}
	ro, err := v.GetRo()
	if err != nil {
		return nil, err
	}
	switch field {
	case "properties":
		base = ro.BaseProperties
	default:
		base = ro.BaseProtocols
	}
	if base.DynList() != nil {
		return []*Item{base.DynList()}, nil
	}
	if base.DynRel() != nil {
		return base.DynRel(), nil
	}
	return nil, nil
}

func (rw *ClassRw) Properties() ([]*Item, error) { return rw.listOf("properties") }
func (rw *ClassRw) Protocols() ([]*Item, error)  { return rw.listOf("protocols") }

func (rw *ClassRw) SetFlags(s uint32)   { rw.FlagsField |= s }
func (rw *ClassRw) ClearFlags(c uint32) { rw.FlagsField &= ^c }

func (rw *ClassRw) casFlags(old uint32, newFlags uint32) bool {
	rw.CasAttempts++
	if rw.CasFailures > 0 {
		rw.CasFailures--
		rw.FlagsField = old ^ 1 // 制造一次并发改写
		return false
	}
	rw.FlagsField = newFlags
	return true
}

func (rw *ClassRw) ChangeFlags(setFlags uint32, clearFlags uint32) error {
	if setFlags&clearFlags != 0 {
		return errors.New("set and clear must not overlap")
	}
	rw.CasAttempts = 0
	for {
		oldf := rw.FlagsField
		newf := (oldf | setFlags) & ^clearFlags
		if rw.casFlags(oldf, newf) {
			return nil
		}
	}
}
