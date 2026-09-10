"""
epoll_echo.py — 基于 selectors 模块的回显服务器

依据 Python 官方文档 docs.python.org/zh-cn/3/library/selectors.html：
  - selectors 建立在 select 模块之上，是官方推荐的 I/O 复用入口；
  - DefaultSelector 会自动选择当前平台上最高效的实现
    （Linux 上是 EpollSelector，即 select.epoll() 的封装；
     macOS/BSD 上是 KqueueSelector；Windows 上是 SelectSelector）；
  - register(fileobj, events, data) 返回 SelectorKey；
    select(timeout) 返回 (key, events) 元组列表。

运行：python3 epoll_echo.py [port]
测试：nc 127.0.0.1 9000
"""

import selectors
import socket
import sys

HOST = "127.0.0.1"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9000


def accept(sock: socket.socket, mask: int) -> None:
    """监听 socket 就绪 → 接受新连接并注册读事件（回调放在 data 里）。"""
    conn, addr = sock.accept()  # 应当已就绪
    print("accepted", conn, "from", addr)
    conn.setblocking(False)
    sel.register(conn, selectors.EVENT_READ, read)  # data=回调函数


def read(conn: socket.socket, mask: int) -> None:
    """已连接 socket 就绪 → 读取并回显。"""
    data = conn.recv(1000)  # 应当已就绪
    if data:
        print("echoing", repr(data), "to", conn)
        conn.send(data)  # 假设不会阻塞（demo 简化）
    else:
        print("closing", conn)
        sel.unregister(conn)  # 官方建议：关闭前先注销
        conn.close()


if __name__ == "__main__":
    # DefaultSelector：Linux → epoll，macOS/BSD → kqueue，Windows → select
    sel = selectors.DefaultSelector()

    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HOST, PORT))
    sock.listen(100)
    sock.setblocking(False)
    sel.register(sock, selectors.EVENT_READ, accept)
    print(f"echo server (DefaultSelector) listening on {HOST}:{PORT}")

    # 事件循环：select() 阻塞直到有 fd 就绪，取出 (key, mask) 逐个回调。
    # selectors 的回调模型是"注册时绑定 data，就绪时取回"——
    # 与 C 里 epoll_event.data 字段的设计思想一致。
    while True:
        events = sel.select()
        for key, mask in events:
            callback = key.data      # register 时存进去的回调
            callback(key.fileobj, mask)
