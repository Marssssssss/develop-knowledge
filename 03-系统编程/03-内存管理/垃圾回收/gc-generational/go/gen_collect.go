// 分代式 GC 的收集动作:minor(只收新生代)/ major(整个堆)/ 触发条件。
//
// 与 Python 版 gen_model.py 的 minor()/major()/step() 一一对应。
package main

// --------------------------------------------------------------------------
// minor GC:只收新生代
// --------------------------------------------------------------------------

func (h *genHeap) minor() map[string]bool {
	h.minorGCs++
	h.tracedWords += h.youngWords() // 成本 ∝ 新生代,与老年代无关
	to := 1 - h.cur
	var dest []*obj
	fwd := map[string]bool{}

	// 根 = mutator 根 + **记忆集**(老年代 -> 新生代 的槽)
	work := []*obj{}
	for _, r := range h.roots {
		if r != nil && r.gen == 0 {
			work = append(work, r)
		}
	}
	for _, ref := range h.remember {
		if t := ref.owner.fields[ref.name]; t != nil && t.gen == 0 {
			work = append(work, t)
		}
	}

	for len(work) > 0 {
		o := work[0]
		work = work[1:]
		if fwd[o.oid] {
			continue
		}
		fwd[o.oid] = true
		o.age++
		promoted := o.age >= h.tenuring
		if promoted {
			o.gen = 1
			h.old = append(h.old, o)
			h.promoted++
		} else {
			dest = append(dest, o)
		}
		for _, name := range sortedKeys(o.fields) {
			if t := o.fields[name]; t != nil && t.gen == 0 {
				if promoted && h.barrier {
					// 晋升**新造**了 老->新 引用,必须补进记忆集
					h.cards[o.oid+"."+name] = true
					h.addRemembered(o, name)
				}
				work = append(work, t)
			}
		}
	}

	h.dropDeadSlots(fwd) // 老年代里指向"已被回收对象"的槽必须清空,否则就是悬空引用
	h.eden = nil
	h.surv[h.cur] = nil
	h.surv[to] = dest
	h.cur = to
	return fwd
}

func (h *genHeap) dropDeadSlots(fwd map[string]bool) {
	var keep []slotRef
	for _, ref := range h.remember {
		t := ref.owner.fields[ref.name]
		if t != nil && t.gen == 0 && !fwd[t.oid] {
			ref.owner.fields[ref.name] = nil
			delete(h.cards, ref.owner.oid+"."+ref.name)
			continue
		}
		keep = append(keep, ref)
	}
	h.remember = keep
}

// --------------------------------------------------------------------------
// major GC:整个堆(不需要记忆集,整堆都在扫描范围内)
// --------------------------------------------------------------------------

func (h *genHeap) major() int {
	h.majorGCs++
	h.tracedWords += h.youngWords() + h.oldWords()
	reachable := map[*obj]bool{}
	work := []*obj{}
	for _, r := range h.roots {
		if r != nil {
			work = append(work, r)
		}
	}
	for len(work) > 0 {
		o := work[len(work)-1]
		work = work[:len(work)-1]
		if reachable[o] {
			continue
		}
		reachable[o] = true
		for _, name := range sortedKeys(o.fields) {
			if t := o.fields[name]; t != nil {
				work = append(work, t)
			}
		}
	}

	before := len(h.young()) + len(h.old)
	var newOld, newEden []*obj
	for _, o := range h.old {
		if reachable[o] {
			newOld = append(newOld, o)
		}
	}
	for _, o := range h.young() {
		if reachable[o] {
			o.gen = 0 // 本模型里新生代幸存者原地留下
			newEden = append(newEden, o)
		}
	}
	h.old = newOld
	h.eden = newEden
	h.surv[h.cur] = nil
	dead := before - len(h.old) - len(h.eden)

	h.remember = nil
	h.cards = map[string]bool{}
	h.rebuildRemembered()
	return dead
}

func (h *genHeap) rebuildRemembered() {
	if !h.barrier {
		return
	}
	for _, o := range h.old {
		for _, name := range sortedKeys(o.fields) {
			if t := o.fields[name]; t != nil && t.gen == 0 {
				h.cards[o.oid+"."+name] = true
				h.addRemembered(o, name)
			}
		}
	}
}

// --------------------------------------------------------------------------
// 触发条件
// --------------------------------------------------------------------------

// step 是"分配之后"的一次容量检查。
//
// 分代模式:新生代(nursery)满 -> minor,只看 nursery 那点预算。
// 非分代基线:**只把这一层关掉** —— 没有 nursery,"堆满"就是整个堆预算满,
// 于是同样的负载下它只能一次一次地做全堆收集。两边共用同一套堆预算。
func (h *genHeap) step() {
	if h.genera {
		if h.youngWords() >= h.youngCap {
			h.minor()
		}
	} else if h.youngWords()+h.oldWords() >= h.heapCap {
		h.major()
	}
	if h.oldWords() >= h.oldCap {
		h.major()
	}
}
