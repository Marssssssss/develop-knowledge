package main

// 时间事件部分：usUntilEarliestTimer / processTimeEvents /
// aeCreateTimeEvent / aeDeleteTimeEvent。
// 从 ae.go 拆出，保持单文件 ≤ 300 行（见 _docs/OPTIMIZATION.md §1.1）。

func UsUntilEarliestTimer(el *EventLoop) int64 {
	te := el.TimeEventHead
	if te == nil {
		return -1
	}
	var earliest *TimeEvent
	for te != nil {
		if (earliest == nil || te.When < earliest.When) && te.ID != AeDeletedEventID {
			earliest = te
		}
		te = te.Next
	}
	if earliest == nil {
		return -1
	}
	now := el.Clock.GetMonotonicUs()
	if now >= earliest.When {
		return 0
	}
	return earliest.When - now
}

func ProcessTimeEvents(el *EventLoop) int {
	processed := 0
	te := el.TimeEventHead
	maxID := el.TimeEventNextID - 1
	now := el.Clock.GetMonotonicUs()

	for te != nil {
		if te.ID == AeDeletedEventID {
			next := te.Next
			if te.Refcount != 0 { // 递归调用中，不释放
				te = next
				continue
			}
			if te.Prev != nil {
				te.Prev.Next = te.Next
			} else {
				el.TimeEventHead = te.Next
			}
			if te.Next != nil {
				te.Next.Prev = te.Prev
			}
			if te.FinalizerProc != nil {
				te.FinalizerProc(el, te.ClientData)
				now = el.Clock.GetMonotonicUs()
			}
			te = next
			continue
		}

		// 本轮新建的时间事件不参与本轮处理
		if te.ID > maxID {
			te = te.Next
			continue
		}

		if te.When <= now {
			te.Refcount++
			retval := te.TimeProc(el, te.ID, te.ClientData)
			te.Refcount--
			processed++
			now = el.Clock.GetMonotonicUs()
			if retval != AeNoMore {
				te.When = now + retval*1000
			} else {
				te.ID = AeDeletedEventID
			}
		}
		te = te.Next
	}
	return processed
}

func AeCreateTimeEvent(el *EventLoop, milliseconds int64, proc func(*EventLoop, int64, interface{}) int64,
	finalizer func(*EventLoop, interface{}), data interface{}) int64 {
	id := el.TimeEventNextID
	el.TimeEventNextID++
	te := &TimeEvent{
		ID:            id,
		When:          el.Clock.GetMonotonicUs() + milliseconds*1000,
		TimeProc:      proc,
		FinalizerProc: finalizer,
		ClientData:    data,
	}
	te.Next = el.TimeEventHead
	if te.Next != nil {
		te.Next.Prev = te
	}
	el.TimeEventHead = te
	return id
}

func AeDeleteTimeEvent(el *EventLoop, id int64) int {
	for te := el.TimeEventHead; te != nil; te = te.Next {
		if te.ID == id {
			te.ID = AeDeletedEventID
			return AeOK
		}
	}
	return AeErr
}
