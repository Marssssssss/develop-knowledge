package main

import "errors"

// ---- list_array_tt 与 ro 上的 list / relative_list_list_t ----

// Item 是 method_list_t / property_list_t / protocol_list_t 的占位对象。
type Item struct {
	Tag   string
	IsDup bool
}

func (i *Item) Duplicate() *Item {
	return &Item{Tag: i.Tag, IsDup: true}
}

// ListOrList 对应 ro->baseMethods 之类的二选一存储。
// 真实类型是 objc::PointerUnion<method_list_t, relative_list_list_t<...>>，
// 同样用最低位做标签。
type ListOrList struct {
	Kind string // "list" 或 "rel"
	List *Item
	Rel  []*Item
}

func (l *ListOrList) DynList() *Item {
	if l.Kind == "list" {
		return l.List
	}
	return nil
}

func (l *ListOrList) DynRel() []*Item {
	if l.Kind == "rel" {
		return l.Rel
	}
	return nil
}

// ListArray 对应 list_array_tt 的四种存储形态。
type ListArray struct {
	Kind   string // "null" / "list" / "array" / "rel"
	Single *Item
	Items  []*Item
}

func newListArray() *ListArray {
	return &ListArray{Kind: "null"}
}

func listArrayFromList(x *Item) *ListArray {
	return &ListArray{Kind: "list", Single: x}
}

func listArrayFromRel(xs []*Item) *ListArray {
	return &ListArray{Kind: "rel", Items: append([]*Item(nil), xs...)}
}

func (a *ListArray) Lists() []*Item {
	switch a.Kind {
	case "list":
		return []*Item{a.Single}
	case "array", "rel":
		return a.Items
	}
	return nil
}

func containsItem(xs []*Item, x *Item) bool {
	for _, e := range xs {
		if e == x {
			return true
		}
	}
	return false
}

func (a *ListArray) AttachLists(added []*Item) error {
	if len(added) == 0 {
		return nil // addedCount == 0：直接 return
	}
	for _, x := range added {
		if containsItem(a.Lists(), x) {
			return errors.New("list attached twice")
		}
	}
	switch {
	case a.Kind == "null" && len(added) == 1: // 0 -> 1
		a.Kind, a.Single = "list", added[0]
	case a.Kind == "null" || a.Kind == "list": // 0/1 -> many
		var old *Item
		if a.Kind == "list" {
			old = a.Single
		}
		arr := make([]*Item, len(added))
		if old != nil {
			arr = append(arr, old) // 老的挪到末尾
		}
		for i, x := range added {
			arr[i] = x // 新的排在最前
		}
		a.Kind, a.Items, a.Single = "array", arr, nil
	case a.Kind == "array": // many -> many
		arr := make([]*Item, len(a.Items)+len(added))
		for i := len(a.Items) - 1; i >= 0; i-- {
			arr[i+len(added)] = a.Items[i]
		}
		for i, x := range added {
			arr[i] = x
		}
		a.Kind, a.Items = "array", arr
	default: // rel -> many
		arr := append(append([]*Item(nil), added...), a.Items...)
		a.Kind, a.Items = "array", arr
	}
	return nil
}

func (a *ListArray) AttachListList(rel []*Item) error {
	if a.Kind != "null" {
		return errors.New("attachListList onto non-empty storage")
	}
	a.Kind, a.Items = "rel", append([]*Item(nil), rel...)
	return nil
}

func (a *ListArray) CopyListList(numLoaded int) error {
	if a.Kind != "rel" || numLoaded == 0 {
		return errors.New("copyListList needs a non-empty relative list")
	}
	xs := append([]*Item(nil), a.Items[:numLoaded]...)
	if len(xs) == 1 {
		a.Kind, a.Single, a.Items = "list", xs[0], nil
		return nil
	}
	a.Kind, a.Items = "array", xs
	return nil
}
