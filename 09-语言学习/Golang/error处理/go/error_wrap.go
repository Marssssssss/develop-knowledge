// error_wrap.go — 单文件演示 Go error 处理范式: error 接口、fmt.Errorf %w、errors.Is/As/Join
// Demo 3: error 处理(error 接口 + 包装链 + Is/As + 自定义 Is 方法 + Join)
package main

import (
	"errors"
	"fmt"
	"io"
	"strconv"
)

// ---------- 自定义 error 类型 + sentinel ----------
var ErrNotFound = errors.New("resource not found") // 哨兵:跨包可比较

type NotFoundError struct {
	Resource string
	ID       int
}

func (e *NotFoundError) Error() string {
	return fmt.Sprintf("%s#%d not found", e.Resource, e.ID)
}

// 自定义 Is 方法: 让 *NotFoundError 匹配 ErrNotFound sentinel
func (e *NotFoundError) Is(target error) bool {
	return target == ErrNotFound
}

// 自定义 Unwrap: 让 NotFoundError 可包装其他 err
type ValidationError struct {
	Field string
	Err   error
}

func (e *ValidationError) Error() string {
	return fmt.Sprintf("validation failed on %s: %v", e.Field, e.Err)
}

func (e *ValidationError) Unwrap() error { return e.Err }

func main() {
	// ---------- 1) 基础: sentinel + errors.New ----------
	fmt.Println("[1] sentinel error + errors.New")
	err1 := errors.New("plain error")
	fmt.Printf("  err1.Error() = %q\n", err1.Error())
	fmt.Printf("  err1 == nil? %v\n", err1 == nil)

	// ---------- 2) 自定义 error 类型携带上下文 ----------
	fmt.Println("\n[2] 自定义 error 类型: 携带 Resource + ID")
	nfe := &NotFoundError{Resource: "user", ID: 42}
	fmt.Printf("  nfe.Error() = %q\n", nfe.Error())

	// ---------- 3) fmt.Errorf %w 包装链 ----------
	fmt.Println("\n[3] fmt.Errorf %%w: 构造包装链")
	wrapped := fmt.Errorf("query user#42: %w", nfe)
	doubleWrapped := fmt.Errorf("api handler: %w", wrapped)
	fmt.Printf("  最外层 err = %q\n", doubleWrapped.Error())
	fmt.Printf("  Unwrap(Unwrap(err)) = %v (回到 sentinel)\n", errors.Unwrap(errors.Unwrap(doubleWrapped)) == nil)

	// ---------- 4) errors.Is 沿链路查找 ----------
	fmt.Println("\n[4] errors.Is: 沿包装链比对 sentinel")
	fmt.Printf("  errors.Is(doubleWrapped, ErrNotFound) = %v (匹配 NotFoundError.Is)\n",
		errors.Is(doubleWrapped, ErrNotFound))
	fmt.Printf("  errors.Is(doubleWrapped, io.EOF) = %v\n", errors.Is(doubleWrapped, io.EOF))

	// ---------- 5) errors.As 提取特定类型 ----------
	fmt.Println("\n[5] errors.As: 沿链提取第一个 *NotFoundError")
	var got *NotFoundError
	if errors.As(doubleWrapped, &got) {
		fmt.Printf("  提取到: Resource=%q ID=%d\n", got.Resource, got.ID)
	}

	// ---------- 6) Unwrap 自定义方法 ----------
	fmt.Println("\n[6] ValidationError.Unwrap() error")
	vErr := &ValidationError{
		Field: "age",
		Err:   errors.New("must be positive"),
	}
	outer := fmt.Errorf("create user: %w", vErr)
	if inner := errors.Unwrap(outer); inner != nil {
		fmt.Printf("  Unwrap(outer) = %v\n", inner)
		fmt.Printf("  Unwrap(Unwrap(outer)) = %v\n", errors.Unwrap(inner))
	}

	// ---------- 7) errors.Join: 多 error 并行合并 (Go 1.20+) ----------
	fmt.Println("\n[7] errors.Join(errs...): 多 error 包装为 Unwrap() []error")
	dbErr := errors.New("db connection lost")
	cacheErr := errors.New("cache miss")
	logErr := errors.New("log write failed")
	joined := errors.Join(dbErr, cacheErr, logErr)
	fmt.Printf("  joined.Error() = %q\n", joined.Error())
	fmt.Printf("  errors.Is(joined, dbErr) = %v\n", errors.Is(joined, dbErr))
	fmt.Printf("  errors.Is(joined, logErr) = %v\n", errors.Is(joined, logErr))
	// errors.As 也能从 join 中提取
	fmt.Printf("  join 类型是 *joinError, 实现 Unwrap() []error\n")

	// ---------- 8) %v vs %w 区别 ----------
	fmt.Println("\n[8] fmt.Errorf %%v vs %%w")
	original := errors.New("disk full")
	strFmt := fmt.Errorf("write file: %v", original) // 只保留字符串, 不包装
	wrapFmt := fmt.Errorf("write file: %w", original) // 包装, 保留链路
	fmt.Printf("  %%v: errors.Is(strFmt, original) = %v\n", errors.Is(strFmt, original))
	fmt.Printf("  %%w: errors.Is(wrapFmt, original) = %v\n", errors.Is(wrapFmt, original))

	// ---------- 9) 自定义 error 的 fmt.Stringer 接口 ----------
	fmt.Println("\n[9] fmt.Print* 自动调用 Error() string")
	tmpErr := &NotFoundError{Resource: "post", ID: 7}
	fmt.Printf("  fmt.Println(tmpErr) = %v\n", tmpErr)
	fmt.Printf("  fmt.Sprintf(%%s, tmpErr) = %s\n", tmpErr)

	// ---------- 10) strconv.NumError + errors.As 实战 ----------
	fmt.Println("\n[10] strconv.NumError: errors.As 提取具体错误类型")
	_, convErr := strconv.Atoi("not_a_number")
	if convErr != nil {
		fmt.Printf("  convErr = %v\n", convErr)
		var numErr *strconv.NumError
		if errors.As(convErr, &numErr) {
			fmt.Printf("  提取 *strconv.NumError: Func=%s Num=%q Err=%v\n",
				numErr.Func, numErr.Num, numErr.Err)
		}
	}
}