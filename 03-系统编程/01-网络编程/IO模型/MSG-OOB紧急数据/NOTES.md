# NOTES — MSG_OOB 紧急数据(README 的完整代码块与补充)

README 为守住 ≤200 行,把两段较长的内核代码移到这里。

## §1 `tcp_check_urg()` 的 "Double Dutch" 完整代码块

`net/ipv4/tcp_input.c`:

```c
	/* We may be adding urgent data when the last byte read was
	 * urgent. To do this requires some care. We cannot just ignore
	 * tp->copied_seq since we would read the last urgent byte again
	 * as data, nor can we alter copied_seq until this data arrives
	 * or we break the semantics of SIOCATMARK (and thus sockatmark())
	 *
	 * NOTE. Double Dutch. Rendering to plain English: author of comment
	 * above did something sort of 	send("A", MSG_OOB); send("B", MSG_OOB);
	 * and expect that both A and B disappear from stream. This is _wrong_.
	 * Though this happens in BSD with high probability, this is occasional.
	 * Any application relying on this is buggy. Note also, that fix "works"
	 * only in this artificial test. Insert some normal data between A and B and we will
	 * decline of BSD again. Verdict: it is better to remove to trap
	 * buggy users.
	 */
	if (tp->urg_seq == tp->copied_seq && tp->urg_data &&
	    !sock_flag(sk, SOCK_URGINLINE) && tp->copied_seq != tp->rcv_nxt) {
		struct sk_buff *skb = skb_peek(&sk->sk_receive_queue);
		tp->copied_seq++;
		if (skb && !before(tp->copied_seq, TCP_SKB_CB(skb)->end_seq)) {
			__skb_unlink(skb, &sk->sk_receive_queue);
			__kfree_skb(skb);
		}
	}

	WRITE_ONCE(tp->urg_data, TCP_URG_NOTYET);
	WRITE_ONCE(tp->urg_seq, ptr);
```

注意最后两句:**先写 `urg_data` 再写 `urg_seq`**,且两次都用 `WRITE_ONCE`。
前面还有一段「Do not replay urg ptr」的注释,提到「指到乱序队列里已有报文」
这种规格未覆盖的情形:内核选择直接忽略,并提示「值得想想是否存在应用级
死锁的 DoS 可能」。

## §2 `tcp_recvmsg()` 的 `urg_offset` / `urg_hole` 完整代码块

`net/ipv4/tcp.c`:

```c
		/* Do we have urgent data here? */
		if (unlikely(tp->urg_data)) {
			u32 urg_offset = tp->urg_seq - *seq;

			if (urg_offset < used) {
				if (!urg_offset) {
					if (!sock_flag(sk, SOCK_URGINLINE)) {
						WRITE_ONCE(*seq, *seq + 1);
						urg_hole++;
						offset++;
						used--;
						goto skip_copy;
					}
				} else
					used = urg_offset;
			}
		}
```

关键点:

- `urg_offset < used` 的截断**不区分** `SOCK_URGINLINE`。
- 只有 `urg_offset == 0`(下一个待读字节就是紧急字节)时才看该标志:
  内联 → 当普通数据拷走;不内联 → `seq++` + `urg_hole++` + `used--` 跳过。
- `urg_hole` 之后参与「已拷贝字节数」的对账(见同函数里
  `peek_seq - peek_offset - copied - urg_hole != tp->copied_seq` 那处判断),
  因为被跳过的那一字节不计入 `copied`。
- 读完紧急字节之后还有一句 `if (urg_data && after(copied_seq, urg_seq))
  WRITE_ONCE(tp->urg_data, 0);` —— 越过紧急位置后状态被清零。

## §3 `tcp_poll()` 里与紧急数据有关的两处

```c
		u16 urg_data = READ_ONCE(tp->urg_data);

		if (unlikely(urg_data) &&
		    READ_ONCE(tp->urg_seq) == READ_ONCE(tp->copied_seq) &&
		    !sock_flag(sk, SOCK_URGINLINE))
			target++;                /* rcvlowat 目标 +1 */
		...
		if (urg_data & TCP_URG_VALID)
			mask |= EPOLLPRI;
```

「在标记处」用 `urg_seq == copied_seq` 判定,与 `SIOCATMARK` 判据**差了一个
`urg_data` 非零的前置**——但因为后面还要 `urg_data` 参与,实际差别仅在
`urg_data == 0` 时(`SIOCATMARK` 给 0,`target` 也不加)。
