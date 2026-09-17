#!/usr/bin/env python3
"""OkHttp 拦截器链与连接池 —— 纯 Python 教学模型 + 自检。

依据 OkHttp 官方文档与源码语义复刻(见 README「参考资料」):
  * 责任链顺序: Application → RetryAndFollowUp → Bridge → Cache → Connect
                → Network → CallServer,响应沿反方向回溯
  * 应用拦截器: 每次 call 恰好一次(缓存命中 / 重定向也只一次)
    网络拦截器: 只对真实发出的网络请求调用(重定向会再调一次,缓存短路则不调)
  * ConnectionPool: 默认最多 5 条空闲连接、空闲 5 分钟淘汰,同 Address 复用连接

运行: python3 okhttp_chain_check.py
"""

from __future__ import annotations

import sys

from okhttp_chain_model import (ApplicationInterceptor, CacheInterceptor, CallServerInterceptor,
                                ConnectionPool, ConnectInterceptor, DEFAULT_KEEP_ALIVE_MS, Engine,
                                NetworkInterceptor, Request, Response, build_client, call)


PASS = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS
    if not cond:
        print(f"FAIL  {label}  {detail}")
        raise AssertionError(label)
    PASS += 1
    print(f"ok    {label}" + (f"  [{detail}]" if detail else ""))


# --------------------------------------------------------------------------- 场景
def scenario_app_vs_network() -> None:
    """官方文档的对照实验:重定向下应用拦截器 1 次、网络拦截器 2 次。"""
    routes = {"http://plain.test/helloworld.txt": (302, {"Location": "https://plain.test/helloworld.txt"}),
              "https://plain.test/helloworld.txt": (200, {})}
    eng = Engine(ConnectionPool())
    app, net = ApplicationInterceptor(eng), NetworkInterceptor(eng)
    ic = build_client(eng, routes, app, net)
    resp = call(ic, "http://plain.test/helloworld.txt", eng)

    check("重定向: 应用拦截器恰好被调用 1 次", eng.app_calls == 1, f"app_calls={eng.app_calls}")
    check("重定向: 网络拦截器被调用 2 次(每个网络请求一次)", eng.network_calls == 2,
          f"network_calls={eng.network_calls}")
    check("重定向: 服务端收到 2 次请求", eng.server_calls == 2, f"server_calls={eng.server_calls}")
    check("重定向: 最终响应 URL 是重定向后的地址", resp.request.url == "https://plain.test/helloworld.txt",
          resp.request.url)


def scenario_network_interceptor_and_cache() -> None:
    """缓存命中短路网络层:应用拦截器仍每次调用,网络/服务端只走第一次。"""
    routes = {"http://cache.test/data": (200, {})}
    eng = Engine(ConnectionPool())
    app, net = ApplicationInterceptor(eng), NetworkInterceptor(eng)
    ic = build_client(eng, routes, app, net)
    call(ic, "http://cache.test/data", eng)
    call(ic, "http://cache.test/data", eng)

    check("缓存命中: 应用拦截器 2 次(每次 call 一次)", eng.app_calls == 2, f"app_calls={eng.app_calls}")
    check("缓存命中: 网络拦截器仍只有 1 次(第二次被短路)", eng.network_calls == 1,
          f"network_calls={eng.network_calls}")
    check("缓存命中: 服务端只收到 1 次请求", eng.server_calls == 1, f"server_calls={eng.server_calls}")
    check("缓存命中: 事件日志出现 cache-hit", eng.tags("cache-hit"), ", ".join(eng.log))


def scenario_short_circuit_and_double_proceed() -> None:
    """应用拦截器两种特权:不调 proceed 即短路;调两次则下游跑两遍。"""
    routes = {"http://retry.test/x": (200, {})}
    eng = Engine(ConnectionPool())
    ic = build_client(eng, routes, ApplicationInterceptor(eng, name="retry", proceed_times=2))
    call(ic, "http://retry.test/x", eng, method="POST")
    check("应用拦截器重复 proceed(POST 不缓存): 服务端被请求 2 次", eng.server_calls == 2,
          f"server_calls={eng.server_calls}")
    check("应用拦截器自身只被调用 1 次", eng.app_calls == 1, f"app_calls={eng.app_calls}")

    eng_get = Engine(ConnectionPool())
    ic_get = build_client(eng_get, routes, ApplicationInterceptor(eng_get, name="retry", proceed_times=2))
    call(ic_get, "http://retry.test/x", eng_get, method="GET")
    check("同样重复 proceed 但方法是 GET: 第二次被 CacheInterceptor 挡住,服务端仅 1 次",
          eng_get.server_calls == 1, f"server_calls={eng_get.server_calls}")

    eng2 = Engine(ConnectionPool())
    cache_first = CacheInterceptor(eng2)
    eng2.cache["http://retry.test/x"] = Response(Request("http://retry.test/x"), 200)
    short_circuit = [cache_first, ConnectInterceptor(eng2), CallServerInterceptor(eng2, routes)]
    call(short_circuit, "http://retry.test/x", eng2)
    check("短路: 首层命中即返回,proceed 未被调用 → 服务端 0 次", eng2.server_calls == 0,
          f"server_calls={eng2.server_calls}")


def scenario_pool_reuse() -> None:
    pool = ConnectionPool()
    c1 = pool.acquire("https://api.test", now_ms=0)
    pool.recycle(c1, 0)
    c2 = pool.acquire("https://api.test", now_ms=100)
    check("连接池: 同 Address 复用同一条连接", c1 is c2 and pool.created == 1 and pool.reused == 1,
          f"created={pool.created} reused={pool.reused}")
    check("连接池: 取出后不再计入空闲", pool.idle_connection_count() == 0,
          f"idle={pool.idle_connection_count()}")


def scenario_pool_idle_limit() -> None:
    pool = ConnectionPool()
    for i in range(6):                       # 6 个不同 Address,逐条归还
        conn = pool.acquire(f"https://h{i}.test", now_ms=i * 10)
        pool.recycle(conn, i * 10)
    pool.cleanup(60)
    check("连接池: 空闲数不超过 maxIdleConnections=5", pool.idle_connection_count() == 5,
          f"idle={pool.idle_connection_count()}")
    check("连接池: 超限时淘汰最久空闲的那条(h0)", pool.evicted == 1 and pool.evicted_addresses == ["https://h0.test"],
          f"evicted={pool.evicted_addresses}")
    check("连接池: 存活连接数同步收敛为 5", pool.connection_count() == 5, f"conns={pool.connection_count()}")


def scenario_pool_keep_alive() -> None:
    pool = ConnectionPool()
    conn = pool.acquire("https://idle.test", now_ms=0)
    pool.recycle(conn, 0)
    pool.cleanup(DEFAULT_KEEP_ALIVE_MS)               # 恰好 5 分钟:未超时
    check("连接池: 空闲 5 分钟整仍保留(判定用 >)", pool.idle_connection_count() == 1,
          f"idle={pool.idle_connection_count()}")
    pool.cleanup(DEFAULT_KEEP_ALIVE_MS + 1)           # 超过 1 ms 即淘汰
    check("连接池: 空闲超时后被淘汰", pool.idle_connection_count() == 0 and pool.evicted == 1,
          f"idle={pool.idle_connection_count()} evicted={pool.evicted}")


def scenario_http2_multiplex() -> None:
    pool = ConnectionPool()
    a = pool.acquire("https://h2.test", now_ms=0, multiplex_ok=True)
    a2 = pool.acquire("https://h2.test", now_ms=1, multiplex_ok=True)
    check("HTTP/2: 未归还的连接也能再复用(多路复用)", a is a2 and pool.created == 1, f"created={pool.created}")
    pool2 = ConnectionPool()
    b = pool2.acquire("https://h1.test", now_ms=0, multiplex_ok=False)
    b2 = pool2.acquire("https://h1.test", now_ms=1, multiplex_ok=False)
    check("HTTP/1.x: 连接被占用时必须新建", b is not b2 and pool2.created == 2, f"created={pool2.created}")


def scenario_end_to_end_pool() -> None:
    """两次真实 call(无缓存)共享连接池 → 只建 1 条连接。"""
    routes = {"http://api.test/a": (200, {}), "http://api.test/b": (200, {})}
    pool = ConnectionPool()
    eng = Engine(pool)
    ic = build_client(eng, routes)
    call(ic, "http://api.test/a", eng)
    pool.recycle(eng.connection, eng.now)
    call(ic, "http://api.test/b", eng)
    check("端到端: 同 host 两次请求只建 1 条连接", pool.created == 1 and pool.reused == 1,
          f"created={pool.created} reused={pool.reused}")


def main() -> int:
    for fn in (scenario_app_vs_network, scenario_network_interceptor_and_cache,
               scenario_short_circuit_and_double_proceed, scenario_pool_reuse,
               scenario_pool_idle_limit, scenario_pool_keep_alive, scenario_http2_multiplex,
               scenario_end_to_end_pool):
        print(f"\n--- {fn.__name__} ---")
        fn()
    print(f"\nALL PASS: {PASS} assertions")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
