package main

import (
	"fmt"
	"math"
)

// checkExporter 覆盖 exporter helper:退避重试预算、发送队列背压与持久化队列。
func checkExporter() {
// ------------------------------------------------------------ H exporter
rp := NewRetryPolicy()
check("H1 默认退避参数", rp.InitialInterval == 5 && rp.MaxInterval == 30 &&
	rp.MaxElapsedTime == 300 && rp.Multiplier == 1.5, "")
sch := rp.Schedule(7)
check("H2 退避序列封顶", len(sch) == 7 && sch[0] == 5 && sch[4] == 25.3125 &&
	sch[6] == 30, fmt.Sprintf("%v", sch))
check("H3 首次等待 5s", rp.Backoff(1) == 5, "")
check("H4 预算内重试 12 次", rp.AttemptsWithinBudget() == 12,
	fmt.Sprintf("%d", rp.AttemptsWithinBudget()))
check("H5 累计等待", near(rp.TotalWait(12), 275.9375), fmt.Sprintf("%.4f", rp.TotalWait(12)))
old := NewRetryPolicy()
old.MaxElapsedTime = 120
check("H6 旧口径 120s -> 6 次", old.AttemptsWithinBudget() == 6,
	fmt.Sprintf("%d", old.AttemptsWithinBudget()))
forever := NewRetryPolicy()
forever.MaxElapsedTime = 0
check("H7 0 表示永不停止", forever.AttemptsWithinBudget() == -1, "")
offPolicy := NewRetryPolicy()
offPolicy.Enabled = false
check("H8 关闭重试", offPolicy.AttemptsWithinBudget() == 0, "")
check("H9 关闭后间隔无穷", math.IsInf(offPolicy.Backoff(1), 1), "")
check("H10 窗口覆盖 60s 故障", rp.WindowCovers(60), "")
shortPolicy := NewRetryPolicy()
shortPolicy.MaxElapsedTime = 12
check("H11 短预算覆盖不了", !shortPolicy.WindowCovers(60), "")
q := NewSendingQueue(5000, 10, false)
check("H12 默认队列参数", q.QueueSize == 5000 && q.NumConsumers == 10 &&
	!q.BlockOnOverflow, "")
allOK := true
for i := 0; i < 5000; i++ {
	if q.Enqueue("b") != "ok" {
		allOK = false
		break
	}
}
check("H13 入队 5000 全成功", allOK, "")
check("H14 第 5001 个被丢弃", q.Enqueue("overflow") == "drop" && q.EnqueueFailed == 1, "")
check("H15 容量归零", q.CapacityLeft() == 0 && q.Enqueued == 5000, "")
qb := NewSendingQueue(2, 10, true)
qb.Enqueue("a")
qb.Enqueue("b")
check("H16 overflow 阻塞而非丢弃", qb.Enqueue("c") == "block" && qb.EnqueueFailed == 0, "")
check("H17 空队列消费 0 秒", NewSendingQueue(10, 10, false).Drain(1) == 0, "")
qd := NewSendingQueue(100, 10, false)
for i := 0; i < 20; i++ {
	qd.Enqueue("b")
}
got := qd.Drain(1.0)
check("H18 20 批 10 消费者 = 2 轮", near(got, 2.0), fmt.Sprintf("%.4f", got))
sz, errH19 := SuggestedQueueSize(60, 100, 1)
check("H19 建议队列长度", errH19 == nil && sz == 6000, fmt.Sprintf("%d", sz))
_, errH20 := SuggestedQueueSize(60, 100, 0)
expectErr("H20 per_batch 非法", errH20)
pq := NewPersistentQueue(10)
pq.Enqueue("b1")
pq.Enqueue("b2")
check("H21 持久化队列恢复", pq.CrashAndRestart() == 2 && pq.Len() == 2, "")
check("H22 纯内存队列崩溃全丢", MemoryQueueCrashLoss(2) == 2, "")
check("H23 恢复后清空盘上副本", len(pq.OnDisk) == 0, "")
}
