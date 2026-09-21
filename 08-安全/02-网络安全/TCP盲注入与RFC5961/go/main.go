package main

import "fmt"

func mkSeg(seq uint32, rst, syn, ack, fin bool, ackSeq uint32) *Segment {
	payload := uint32(0)
	var f, s uint32
	if fin {
		f = 1
	}
	if syn {
		s = 1
	}
	return &Segment{Seq: seq, EndSeq: seq + payload + f + s,
		RST: rst, SYN: syn, ACK: ack, FIN: fin, AckSeq: ackSeq}
}

func probe(seq uint32, rst, syn bool, state int) (string, string) {
	sk := NewSock(1000, 32768, 1000, 1000, 65536, state)
	return sk.ValidateIncoming(mkSeg(seq, rst, syn, false, false, 0),
		NewNetns(1000, 500), 1000, 100000, fixedRand(1000))
}

func main() {
	fmt.Println("== 1. RST 三分支（RCV.NXT=1000, RCV.WND=32768）==")
	for _, s := range []uint32{999, 1000, 1001, 20000, 33768, 33769} {
		a, d := probe(s, true, false, tcpEstablished)
		fmt.Printf("   RST seq=%-7d -> %s / %s\n", s, a, d)
	}
	a, d := probe(999, true, false, tcpCloseWait)
	fmt.Printf("   CLOSE_WAIT 下 seq=999 -> %s / %s\n", a, d)

	fmt.Println("\n== 2. SYN 与 RST 的判据差异 ==")
	for _, s := range []uint32{999, 1000, 1001, 99999} {
		a, d := probe(s, false, true, tcpEstablished)
		fmt.Printf("   SYN seq=%-7d -> %s / %s\n", s, a, d)
	}

	fmt.Println("\n== 3. ACK 可接受区间 ==")
	sk := NewSock(1000, 32768, 1000, 1000, 65536, tcpEstablished)
	fmt.Printf("   取值个数 RFC793=%d RFC5961=%d\n",
		sk.AckWindowSize(false), sk.AckWindowSize(true))
	for _, x := range []uint32{1000, 1001, 99000, 1000 - 65536, 1000 - 65537, 1000 - 70000} {
		fmt.Printf("   ACK=%-12d RFC793=%-5v RFC5961=%v\n",
			x, sk.AckAcceptableRFC793(x), sk.AckAcceptableRFC5961(x))
	}

	fmt.Println("\n== 4. challenge ACK 限速（limit=1000, half=500）==")
	for _, rnd := range []uint32{500, 1000, 1499} {
		net := NewNetns(1000, 500)
		n := 0
		for net.ChallengeAckAllowed(1, fixedRand(rnd)) {
			n++
		}
		fmt.Printf("   随机初值=%-5d -> 该秒允许 %d 次\n", rnd, n)
	}

	fmt.Println("\n== 5. 盲注扫描平均包数（N=2^32）==")
	for _, w := range []uint32{32768, 65535} {
		fmt.Printf("   窗口 %-6d 无缓解=%.1f 缓解后=%.0f 放大=%.0f\n", w,
			MeanTriesContinuous(32, w, false), MeanTriesContinuous(32, w, true),
			MeanTriesContinuous(32, w, true)/MeanTriesContinuous(32, w, false))
	}

	fmt.Println("\n== 6. 小规模穷举（N=2^12, WND=64）==")
	var sumNo, sumEx float64
	n := uint64(1) << 12
	for s := uint64(0); s < n; s++ {
		sumNo += float64(SweepClosedForm(12, 64, false, 1234, uint32(s)))
		sumEx += float64(SweepClosedForm(12, 64, true, 1234, uint32(s)))
	}
	fmt.Printf("   窗口判据=%.4f  精确判据=%.4f\n", sumNo/float64(n), sumEx/float64(n))
}
