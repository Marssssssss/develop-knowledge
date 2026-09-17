#!/usr/bin/env python3
"""OkHttp 拦截器链与连接池 —— 纯 Python 模型(责任链 + ConnectionPool)。

由 okhttp_chain_check.py 拆分而来(仅做行搬运,代码逐字节不变):本文件提供 Request /
Response / ConnectionPool / Chain 与七个拦截器;自检断言见 okhttp_chain_check.py。

运行: python3 okhttp_chain_check.py
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_MAX_IDLE = 5
DEFAULT_KEEP_ALIVE_MS = 5 * 60 * 1000  # ConnectionPool.kt: keepAliveDuration = 5 MINUTES

# --------------------------------------------------------------------------- 数据
@dataclass
class Request:
    url: str
    method: str = "GET"
    headers: dict = field(default_factory=dict)

    def rebuild(self, url: str | None = None, method: str | None = None, headers: dict | None = None):
        return Request(url if url is not None else self.url,
                       method if method is not None else self.method,
                       dict(headers if headers is not None else self.headers))


@dataclass
class Response:
    request: Request
    code: int
    headers: dict = field(default_factory=dict)
    body: str = ""


def address_of(url: str) -> str:
    """OkHttp 的 Address ≈ URL 的 scheme + host + port(其余静态配置来自 OkHttpClient)。"""
    scheme, rest = url.split("://", 1)
    host = rest.split("/", 1)[0]
    return f"{scheme}://{host}"


# --------------------------------------------------------------------------- 连接池
class Connection:
    __slots__ = ("address", "cid")

    def __init__(self, address: str, cid: int):
        self.address, self.cid = address, cid


class ConnectionPool:
    """策略层:空闲上限 + 空闲超时淘汰 + 同 Address 复用。"""

    def __init__(self, max_idle: int = DEFAULT_MAX_IDLE,
                 keep_alive_ms: int = DEFAULT_KEEP_ALIVE_MS):
        self.max_idle, self.keep_alive_ms = max_idle, keep_alive_ms
        self._next_id, self._all, self._idle = 1, [], []   # _idle: [(conn, idle_since_ms)]
        self.created = self.reused = self.evicted = 0
        self.evicted_addresses: list[str] = []

    def _evict(self, conn: Connection) -> None:
        if conn in self._all:
            self._all.remove(conn)
        self.evicted += 1
        self.evicted_addresses.append(conn.address)

    def cleanup(self, now_ms: int) -> None:
        """先淘汰空闲超过 keepAlive 的,再淘汰超出 maxIdleConnections 的最久空闲项。"""
        kept = []
        for conn, since in self._idle:
            self._evict(conn) if now_ms - since > self.keep_alive_ms else kept.append((conn, since))
        self._idle = kept
        while len(self._idle) > self.max_idle:
            oldest = min(self._idle, key=lambda it: it[1])
            self._idle.remove(oldest)
            self._evict(oldest[0])

    def acquire(self, address: str, now_ms: int, multiplex_ok: bool = True) -> Connection:
        self.cleanup(now_ms)
        for i, (conn, _since) in enumerate(self._idle):
            if conn.address == address:
                self._idle.pop(i)
                self.reused += 1
                return conn
        if multiplex_ok:            # HTTP/2 复用同一 socket 上的并发 stream
            for conn in self._all:
                if conn.address == address:
                    self.reused += 1
                    return conn
        conn = Connection(address, self._next_id)
        self._next_id += 1
        self._all.append(conn)
        self.created += 1
        return conn

    def recycle(self, conn: Connection, now_ms: int) -> None:
        self._idle.append((conn, now_ms))

    def idle_connection_count(self) -> int:
        return len(self._idle)

    def connection_count(self) -> int:
        return len(self._all)


# --------------------------------------------------------------------------- 引擎与链
class Engine:
    """一次 call 共享的可变状态:连接池、缓存、事件日志、三类计数。"""

    def __init__(self, pool: ConnectionPool, now_ms: int = 0):
        self.pool, self.now = pool, now_ms
        self.connection: Connection | None = None
        self.cache: dict[str, Response] = {}
        self.log: list[str] = []
        self.app_calls = self.network_calls = self.server_calls = 0

    def tags(self, prefix: str) -> list[str]:
        return [e for e in self.log if e.startswith(prefix)]


class Chain:
    """RealInterceptorChain 的等价物:proceed() 构造 index+1 的链并调用第 index 个拦截器。"""

    def __init__(self, interceptors, index: int, request: Request, engine: Engine):
        self.interceptors, self.index = interceptors, index
        self._request, self.engine = request, engine

    def request(self) -> Request:
        return self._request

    def connection(self) -> Connection | None:
        return self.engine.connection

    def proceed(self, request: Request | None = None) -> Response:
        if self.index >= len(self.interceptors):
            raise AssertionError("chain exhausted")
        nxt = Chain(self.interceptors, self.index + 1, request or self._request, self.engine)
        return self.interceptors[self.index].intercept(nxt)


class ApplicationInterceptor:
    """addInterceptor():每次 call 调一次;可短路,可多次 proceed。"""

    def __init__(self, engine: Engine, name: str = "App", proceed_times: int = 1):
        self.engine, self.name, self.proceed_times = engine, name, proceed_times

    def intercept(self, chain: Chain) -> Response:
        self.engine.app_calls += 1
        self.engine.log.append(f"app:{self.name}")
        req = chain.request().rebuild(headers={**chain.request().headers, "X-Trace": "app"})
        response = None
        for _ in range(self.proceed_times):
            response = chain.proceed(req)
        return response


class RetryAndFollowUpInterceptor:
    def __init__(self, engine: Engine, max_follow_ups: int = 5):
        self.engine, self.max_follow_ups = engine, max_follow_ups

    def intercept(self, chain: Chain) -> Response:
        self.engine.log.append("retry-and-follow-up")
        request, response, hops = chain.request(), chain.proceed(chain.request()), 0
        while response.code in (301, 302, 303, 307, 308) and hops < self.max_follow_ups:
            location = response.headers.get("Location")
            if not location:
                break
            hops += 1
            self.engine.log.append(f"follow:{location}")
            request = Request(url=location, method="GET")
            response = chain.proceed(request)
        return response


class BridgeInterceptor:
    def intercept(self, chain: Chain) -> Response:
        req = chain.request()
        headers = dict(req.headers)
        for k, v in (("Host", address_of(req.url).split("://", 1)[1]),
                     ("Accept-Encoding", "gzip"), ("Connection", "Keep-Alive")):
            headers.setdefault(k, v)
        return chain.proceed(req.rebuild(headers=headers))


class CacheInterceptor:
    """命中即短路:不调用 chain.proceed(),其下游(Connect/Network/CallServer)全部不执行。"""

    def __init__(self, engine: Engine):
        self.engine = engine

    def intercept(self, chain: Chain) -> Response:
        req = chain.request()
        if req.method == "GET" and req.url in self.engine.cache:
            self.engine.log.append("cache-hit")
            return self.engine.cache[req.url]
        resp = chain.proceed(req)
        if req.method == "GET" and resp.code == 200:
            self.engine.cache[req.url] = resp
        return resp


class ConnectInterceptor:
    def __init__(self, engine: Engine):
        self.engine = engine

    def intercept(self, chain: Chain) -> Response:
        req = chain.request()
        conn = self.engine.pool.acquire(address_of(req.url), self.engine.now)
        self.engine.connection = conn
        self.engine.log.append(f"connect:reuse={self.engine.pool.reused > 0}")
        return chain.proceed(req)


class NetworkInterceptor:
    """addNetworkInterceptor():每次真实网络请求调一次;链上 connection() 非空。"""

    def __init__(self, engine: Engine, name: str = "Net"):
        self.engine, self.name = engine, name

    def intercept(self, chain: Chain) -> Response:
        self.engine.network_calls += 1
        self.engine.log.append(f"network:{self.name}")
        assert chain.connection() is not None, "network interceptor must observe a live connection"
        return chain.proceed(chain.request())


class CallServerInterceptor:
    """终点:真正收发 HTTP。"""

    def __init__(self, engine: Engine, routes: dict[str, tuple[int, dict]]):
        self.engine, self.routes = engine, routes

    def intercept(self, chain: Chain) -> Response:
        self.engine.server_calls += 1
        req = chain.request()
        code, headers = self.routes.get(req.url, (404, {}))
        self.engine.log.append(f"server:{req.method} {req.url} -> {code}")
        return Response(req, code, dict(headers), body="payload")


def build_client(engine: Engine, routes: dict, app: ApplicationInterceptor | None = None,
                 net: NetworkInterceptor | None = None):
    """按官方源码 getResponseWithInterceptorChain() 的顺序装配拦截器列表。"""
    interceptors = []
    if app:
        interceptors.append(app)
    interceptors += [RetryAndFollowUpInterceptor(engine), BridgeInterceptor(), CacheInterceptor(engine),
                     ConnectInterceptor(engine)]
    if net:
        interceptors.append(net)
    interceptors.append(CallServerInterceptor(engine, routes))
    return interceptors


def call(interceptors, url: str, engine: Engine, method: str = "GET") -> Response:
    engine.connection = None
    return Chain(interceptors, 0, Request(url=url, method=method), engine).proceed()

