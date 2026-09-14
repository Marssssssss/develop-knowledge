// context_demo.go — 单文件串讲 context:取消树 / 超时 / select 并发等待 / Value
// Demo 5: context 包(请求范围的取消信号、超时与元数据传播)
// 模式全部来自官方博客《Go Concurrency Patterns: Context》(Sameer Ajmani, 2014):
//   - Context 接口四方法 Done/Err/Deadline/Value
//   - Background 是树的根,With* 派生子节点,父取消传播到所有后代
//   - select 同 channel 与 ctx.Done() 竞争 —— 取消即返回
//   - Context 没有 Cancel 方法:收信号的一方不是发信号的一方
package main

import (
	"context"
	"fmt"
	"time"
)

// ---------- 1) Context 接口的四方法 ----------
// Done():返回一个 channel,取消或超时时被"关闭"(关闭是对所有监听者的广播)
// Err():Done 关闭后返回取消原因(context.Canceled / DeadlineExceeded)
// Deadline():返回截止时刻与是否存在
// Value():取请求范围的键值
func demoInterface() {
	fmt.Println("[1] Context 接口四方法")
	ctx := context.Background() // 树的根:永不取消、无 deadline、无值
	done := ctx.Done()
	fmt.Printf("  Background: Done()==nil? %v, Err()==nil? %v\n",
		done == nil, ctx.Err() == nil)
	d, ok := ctx.Deadline()
	fmt.Printf("  Background: Deadline 存在? %v(%v)\n", ok, d)
}

// ---------- 2) WithCancel:派生树 + 父取消向下传播 ----------
func demoCancelTree() {
	fmt.Println("\n[2] WithCancel 与取消传播")
	root, cancel := context.WithCancel(context.Background())
	// 语义:parent.Done 关闭或 cancel() 被调用,root.Done 随之关闭
	child, _ := context.WithCancel(root)   // 子 1(未持有 cancel)
	grand, _ := context.WithCancel(child)  // 孙(经子间接挂到根)
	go func() {
		<-root.Done() // 监听 Done:关闭即收到信号
		fmt.Println("  root 收到取消信号, Err =", root.Err())
	}()
	go func() {
		<-child.Done()
		fmt.Println("  child 也收到(父取消传播到所有派生 Context)")
	}()
	go func() {
		<-grand.Done()
		fmt.Println("  grand 也收到(传播可跨多层)")
	}()
	time.Sleep(50 * time.Millisecond) // 等 goroutine 就位
	cancel()                          // 只取消以 root 为根的整棵子树
	time.Sleep(50 * time.Millisecond)
	fmt.Println("  背景 Background 不受影响:",
		context.Background().Err() == nil)
}

// ---------- 3) select 模式:干活的 channel 与取消信号竞争 ----------
// 官方博客 httpDo 的模式:select 同时等结果 channel 与 ctx.Done(),
// 谁先到听谁的;取消后仍等待工作 goroutine 返回(回收资源)。
func work(ctx context.Context, cost time.Duration) <-chan string {
	c := make(chan string, 1)
	go func() {
		// 模拟一段可被取消打断的工作(生产中是 RPC/DB 查询)
		select {
		case <-time.After(cost):
			c <- "工作完成"
		case <-ctx.Done():
			c <- "工作中途退出(检测到取消)"
		}
	}()
	return c
}

func demoSelectPattern() {
	fmt.Println("\n[3] select 取消模式")
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel() // 官方惯例:拿到 cancel 立刻 defer,防泄漏
	// WithTimeout 语义:Done 在 parent.Done / cancel() / 超时 三者最早时关闭;
	// 新 Deadline = min(now+timeout, parent 的 deadline)

	select {
	case r := <-work(ctx, 30*time.Millisecond): // 30ms < 100ms:先干完
		fmt.Println("  场景 A:", r)
	case <-ctx.Done():
		fmt.Println("  场景 A 被取消:", ctx.Err())
	}

	select {
	case r := <-work(ctx, 500*time.Millisecond): // 500ms > 100ms:被超时打断
		fmt.Println("  场景 B:", r)
	case <-ctx.Done():
		fmt.Println("  场景 B 被取消:", ctx.Err()) // context deadline exceeded
	}
}

// ---------- 4) 为什么没有 Cancel 方法:子不该能取消父 ----------
func demoNoCancelMethod() {
	fmt.Println("\n[4] 为什么 Context 没有 Cancel 方法")
	// 官方博客:接收取消信号的一方通常不是发出信号的一方;
	// Done channel 是 receive-only 的同理。子操作不应有能力取消父操作,
	// WithCancel 把"发信号"能力单独交给派生者(cancel 函数不出现在接口上)。
	fmt.Println("  CancelFunc 是独立返回值而非接口方法 -> 子拿不到父的 cancel")
}

// ---------- 5) WithValue:请求范围元数据(非可选参数通道)----------
// key 类型用未导出自定义类型,防跨包冲突(官方 userip 包的模式)
type ctxKey int

const userIPKey ctxKey = 0

func withUserIP(ctx context.Context, ip string) context.Context {
	return context.WithValue(ctx, userIPKey, ip)
}

func userIP(ctx context.Context) (string, bool) {
	ip, ok := ctx.Value(userIPKey).(string) // comma-ok 断言,缺省零值+false
	return ip, ok
}

func demoValue() {
	fmt.Println("\n[5] WithValue")
	ctx := context.Background()
	if _, ok := userIP(ctx); !ok {
		fmt.Println("  无值时 Value 返回 nil,断言 ok=false(不 panic)")
	}
	ctx = withUserIP(ctx, "10.0.0.7")
	ip, ok := userIP(ctx)
	fmt.Printf("  写入后取回: ip=%s ok=%v\n", ip, ok)
	// 值沿树向下可见:派生子 Context 能读到父的值
	child, _ := context.WithCancel(ctx)
	ip2, _ := userIP(child)
	fmt.Println("  子 Context 能读到父写入的值:", ip2)
}

// ---------- 6) Google 实践:Context 作为第一个参数 ----------
func handleRequest(ctx context.Context) string {
	// 官方博客:Google 要求每个跨入站/出站请求的函数都带 ctx 作为
	// 第一个参数,让多团队代码互操作、超时与凭据统一传播。
	if ctx.Err() != nil {
		return "请求未开始就被取消(Deadline 允许先判断值不值得开工)"
	}
	select {
	case <-work(ctx, 10*time.Millisecond):
		return "处理完成"
	case <-ctx.Done():
		return "处理被取消:" + ctx.Err().Error()
	}
}

func demoFirstParam() {
	fmt.Println("\n[6] Context 作为第一个参数")
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()
	fmt.Println(" ", handleRequest(ctx))
	ctx2, cancel2 := context.WithCancel(context.Background())
	cancel2() // 预先取消
	fmt.Println(" ", handleRequest(ctx2))
}

func main() {
	demoInterface()
	demoCancelTree()
	demoSelectPattern()
	demoNoCancelMethod()
	demoValue()
	demoFirstParam()
}
