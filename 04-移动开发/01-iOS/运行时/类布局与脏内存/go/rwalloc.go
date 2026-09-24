package main

// ExtAlloc 把 ro 的只读内容搬进新分配的 rwe（脏内存）。
func ExtAlloc(rw *ClassRw, ro *ClassRo, deep bool) (*ClassRwExt, error) {
	rwe := newClassRwExt()
	if ro.FlagsField&RoMeta != 0 {
		rwe.Version = 7
	} else {
		rwe.Version = 0
	}

	if lst := ro.BaseMethods.DynList(); lst != nil {
		if deep {
			lst = lst.Duplicate()
		}
		if err := rwe.Methods.AttachLists([]*Item{lst}); err != nil {
			return nil, err
		}
	} else if rel := ro.BaseMethods.DynRel(); rel != nil {
		if deep {
			for _, e := range rel { // 逐个 duplicate，逐个 attach
				if err := rwe.Methods.AttachLists([]*Item{e.Duplicate()}); err != nil {
					return nil, err
				}
			}
		} else {
			if err := rwe.Methods.AttachListList(rel); err != nil {
				return nil, err
			}
		}
	}

	// 源码注释：property / protocol 列表「历史上从不深拷贝」，
	// 并说 "This is probably wrong and ought to be fixed some day"
	for _, pair := range []struct {
		dst *ListArray
		src *ListOrList
	}{{rwe.Properties, ro.BaseProperties}, {rwe.Protocols, ro.BaseProtocols}} {
		if lst := pair.src.DynList(); lst != nil {
			if err := pair.dst.AttachLists([]*Item{lst}); err != nil {
				return nil, err
			}
		} else if rel := pair.src.DynRel(); rel != nil {
			if err := pair.dst.AttachListList(rel); err != nil {
				return nil, err
			}
		}
	}

	rw.setRoOrRweRwe(rwe, ro)
	return rwe, nil
}

func ExtAllocIfNeeded(rw *ClassRw) (*ClassRwExt, error) {
	v := rw.GetRoOrRwe()
	if v.IsRwe() {
		return v.GetRwe()
	}
	ro, err := v.GetRo()
	if err != nil {
		return nil, err
	}
	return ExtAlloc(rw, ro, false)
}

// SetDemangledName 对应 CompareAndSwap(nullptr, de ?: mangled, &rwe->demangledName)。
// 返回 (是否成功, 调用方是否应 free(de))。
func SetDemangledName(rwe *ClassRwExt, de string, mangled string, hasDe bool) (bool, bool) {
	want := mangled
	if hasDe {
		want = de
	}
	if rwe.DemangledName != "" {
		return false, hasDe // 失败，de 非空则由调用方释放
	}
	rwe.DemangledName = want
	return true, false
}
