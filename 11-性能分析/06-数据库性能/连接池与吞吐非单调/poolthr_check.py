# -*- coding: utf-8 -*-
"""连接池与吞吐断言(模式语义/fd 公式/Little 定律/非单调曲线)。"""

from poolthr import (
    POOLING_MODES, best_pool_size, fd_limit_needed, little_N,
    multiplex_capacity, throughput_curve,
)

PASS = []


def ok(msg):
    PASS.append(msg)
    print(f"  [ok] {msg}")


def main():
    print("1. 三档池化语义(pgbouncer features)")
    assert "独占" in POOLING_MODES["session"] and "全部" in POOLING_MODES["session"]
    assert "事务期间" in POOLING_MODES["transaction"]
    assert "语句" in POOLING_MODES["statement"]
    ok("session=整连接独占(全特性);transaction=事务粒度(破坏会话级特性);"
       "statement=语句粒度(最激进)")

    print("2. 复用数学")
    assert multiplex_capacity("session", 100, 20) == 20
    assert multiplex_capacity("transaction", 100, 20) == 100
    ok("100 客户端 × 池 20:session 模式只有 20 个客户端能活动;"
       "transaction 模式全体可轮转(事务间隙归还连接)")

    print("3. fd 上限公式(config.html)")
    single = fd_limit_needed(1000, 20, 4, users=1)
    multi = fd_limit_needed(1000, 20, 4, users=8)
    assert single == 1000 + 20 * 4
    assert multi == 1000 + 20 * 4 * 8
    ok("理论最大 fd = max_client_conn + pool_size×库×用户;"
       "共用一个 DB 用户时用户数=1(每用户独立连库则相乘)")

    print("4. Little 定律(算术恒等式)")
    assert little_N(200, 0.05) == 10            # 200 qps × 50ms = 10 在途
    assert abs(little_N(500, 0.2) - 100) < 1e-9
    ok("N = X × R:500qps×200ms=100 在途请求——想要更低延迟要么加吞吐要么减在途")

    print("5. 吞吐非单调曲线(模型)")
    demand, capacity, sc = 0.02, 8, 0.15
    assert throughput_curve(8, capacity, demand, sc) == 8 / demand      # 峰前线性
    assert throughput_curve(4, capacity, demand, sc) == 4 / demand
    x16 = throughput_curve(16, capacity, demand, sc)
    x8 = throughput_curve(8, capacity, demand, sc)
    assert x16 < x8
    best_n, best_x = best_pool_size(capacity, demand, sc, ceiling=64)
    assert best_n == capacity
    ok(f"连接数≤容量线性扩展;超过后付上下文切换税:16 连接({x16:.0f}qps)"
       f"< 8 连接({x8:.0f}qps),峰值池大小=容量 {capacity}")

    print("6. Little 定律与池上限联动")
    need = little_N(300, 0.1)                   # 30 在途
    served = multiplex_capacity("transaction", 300, 20)
    assert served == 300 and need > 20
    ok("在途 30 > 池 20:请求必排队——要么池接近容量上限,要么降单查询 R,"
       "盲目加客户端连接只会拉长队列(吞吐上限由容量决定)")

    print(f"\n共 {len(PASS)} 项断言全部通过")


if __name__ == "__main__":
    main()
