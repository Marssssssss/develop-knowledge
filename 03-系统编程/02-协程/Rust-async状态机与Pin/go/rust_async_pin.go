// rust_async_pin.go — 与 python/rust_async_pin.py 同语义的 Go 复刻(静态审查用)。
package main

import "fmt"

type Value struct {
	Name    string
	Addr    int
	IsUnpin bool
	Pinned  bool
}

var nextAddr int

func NewValue(name string, isUnpin bool) *Value {
	nextAddr++
	return &Value{Name: name, Addr: nextAddr, IsUnpin: isUnpin}
}

// PinNew 只对 Unpin 指向物安全(官方签名约束)。
func PinNew(v *Value) error {
	if !v.IsUnpin {
		return fmt.Errorf("Pin::new requires the pointee to be Unpin")
	}
	v.Pinned = true
	return nil
}

// BoxPin:堆上固定(pinning Box),指针可动、指向物不动。
func BoxPin(v *Value) { v.Pinned = true }

// TryMove:移动已固定的 !Unpin 值违反保证。
func TryMove(v *Value) (int, int, error) {
	if v.Pinned && !v.IsUnpin {
		return 0, 0, fmt.Errorf("moving pinned !Unpin value violates the guarantee")
	}
	old := v.Addr
	nextAddr++
	v.Addr = nextAddr
	return old, v.Addr, nil
}

func main() {
	if err := PinNew(NewValue("plain", true)); err != nil {
		fmt.Println("unexpected:", err)
	}
	if err := PinNew(NewValue("marker", false)); err != nil {
		fmt.Println("Pin::new on !Unpin:", err)
	}
	v := NewValue("selfref", false)
	BoxPin(v)
	if _, _, err := TryMove(v); err != nil {
		fmt.Println("move pinned:", err)
	}
}
