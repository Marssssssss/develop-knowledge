// defer_panic.go — 单文件演示 defer LIFO + panic 触发 + recover 硬性规则
// Demo 4: defer / panic / recover(栈展开 + 资源清理 + recover 规则 + runtime.Goexit)
package main

import (
	"errors"
	"fmt"
	"runtime"
	"time"
)

func main() {
	// ---------- 1) defer LIFO 逆序执行 ----------
	fmt.Println("[1] defer LIFO: 多个 defer 逆序执行")
	func() {
		defer fmt.Println("  defer #1 (A) — 最后执行")
		defer fmt.Println("  defer #2 (B) — 中间")
		defer fmt.Println("  defer #3 (C) — 最先执行")
	}()

	// ---------- 2) defer 参数立即求值 ----------
	fmt.Println("\n[2] defer 参数立即求值(快照)")
	func() {
		i := 0
		defer fmt.Printf("  defer 看到 i = %d (i=0 立即快照)\n", i) // 立刻 i=0 快照
		i = 99
		fmt.Printf("  函数体内 i = %d (改值不影响 defer)\n", i)
	}()

	// ---------- 3) defer 修改命名返回值 ----------
	fmt.Println("\n[3] defer 可修改命名返回值")
	doubleVal := double(5)
	fmt.Printf("  double(5) = %d (return x+1; defer result*=2)\n", doubleVal)

	// ---------- 4) panic + recover 标准模式 ----------
	fmt.Println("\n[4] panic + recover: 标准 recover 模板")
	r := safeCall(func() {
		fmt.Println("  inside func: about to panic")
		panic("oh no!")
	})
	fmt.Printf("  recovered value = %v\n", r)

	// ---------- 5) 资源清理: defer 保证 Close ----------
	fmt.Println("\n[5] defer 资源清理: 即使 panic 也会执行")
	withFakeResource("fileA", func() error {
		fmt.Println("  业务逻辑执行中")
		// 即使下面 panic, fakeResource.Close 仍会执行
		panic(errors.New("业务出错"))
		return nil
	})
	// 即使 panic 也会调用 defer, 资源一定释放

	// ---------- 6) recover 硬性规则: 必须直接 defer 内调用 ----------
	fmt.Println("\n[6] recover 硬性规则: 只在直接 defer 内调用才生效")
	tryWrongRecover() // 失败: recover 在普通函数里调用, 无效

	tryRightRecover() // 成功: recover 在 defer 内匿名函数中

	// ---------- 7) panic 沿调用栈向上传播 ----------
	fmt.Println("\n[7] panic 栈展开: 每层 defer 都执行")
	func() {
		defer func() {
			fmt.Println("  outer defer: panic 经过了这里")
		}()
		inner()
	}()

	// ---------- 8) runtime.Goexit: 退出 goroutine 不 panic ----------
	fmt.Println("\n[8] runtime.Goexit: 退出当前 goroutine, 仍执行 defer 链")
	go func() {
		defer fmt.Println("  goroutine defer 执行 (Goexit 也会触发)")
		fmt.Println("  goroutine start")
		runtime.Goexit() // 退出 goroutine, 不会触发 panic
		fmt.Println("  不可达 (Goexit 之后)")
	}()
	time.Sleep(50 * time.Millisecond)

	// ---------- 9) panic 值可以是任意类型 ----------
	fmt.Println("\n[9] panic 值: 任意类型都能传给 panic/recover")
	tryPanicValues()

	fmt.Println("\n[done] 程序继续执行 (所有 panic 都已 recover)")
}

// double 演示 defer 修改命名返回值
func double(x int) (result int) {
	defer func() {
		result *= 2
	}()
	return x + 1
}

// safeCall 是 panic/recover 标准模式: 包装 f 调用, 返回 panic 值(或 nil)
func safeCall(f func()) (r interface{}) {
	defer func() {
		// recover 必须在 defer 内直接调用才生效
		r = recover()
	}()
	f()
	return nil // 不会执行到这里
}

// withFakeResource 演示资源清理 defer(即使 panic 也释放)
func withFakeResource(name string, fn func() error) (err error) {
	fmt.Printf("  acquire resource %s\n", name)
	defer func() {
		// 这里即使 fn() panic, 也会执行
		if r := recover(); r != nil {
			fmt.Printf("  caught panic in withFakeResource: %v\n", r)
			err = fmt.Errorf("wrapped: %v", r)
		}
		fmt.Printf("  release resource %s (deferred)\n", name)
	}()
	return fn()
}

// tryWrongRecover 错误示例: recover 在普通函数内调用
func tryWrongRecover() {
	defer funcWrapper() // wrapper 内调 recover, 但 wrapper 不在 defer 内
	panic("test")
}

// funcWrapper recover 在普通函数里, 无效
func funcWrapper() {
	if r := recover(); r != nil {
		fmt.Println("  recover 在普通函数内调用: 无效 (panic 仍继续传播)")
	}
}

// tryRightRecover 正确示例: recover 直接在 defer 匿名函数内
func tryRightRecover() {
	defer func() {
		if r := recover(); r != nil {
			fmt.Println("  recover 在 defer 匿名函数内: 有效, 阻止 panic 传播")
		}
	}()
	panic("right-recover test")
}

// inner 触发 panic, 让上层演示栈展开
func inner() {
	defer func() {
		fmt.Println("  inner defer: panic 已经传到这里")
	}()
	panic("from inner")
}

// tryPanicValues 演示 panic 值可以是任意类型
func tryPanicValues() {
	for _, v := range []interface{}{
		"string panic",
		42,
		errors.New("error panic"),
		struct{ Msg string }{"custom struct panic"},
	} {
		func(v interface{}) {
			defer func() {
				if r := recover(); r != nil {
					fmt.Printf("  recovered %T: %v\n", r, r)
				}
			}()
			panic(v)
		}(v)
	}
}