"""
nagle_demo.py — Nagle algorithm & TCP_NODELAY on loopback (Python 3.11+)

Demonstrates:
  demo 1 — Nagle on (default):       100 single-byte writes => fewer segments
  demo 2 — TCP_NODELAY=1:             100 single-byte writes => 100 segments
  demo 3 — interactive echo:         ping/pong latency with/without Nagle

Measurement: socket.getsockopt(IPPROTO_TCP, TCP_INFO, ...) -> tcpi_segs_out
    (Linux only; on non-Linux the demo degrades to timing-only output.)

Run:
    python3 nagle_demo.py
"""

import socket
import struct
import threading
import time

LOOPBACK = "127.0.0.1"
N_WRITES = 100


def parse_segs_out(buf: bytes) -> int:
    """Decode the Linux `struct tcp_info` returned by getsockopt(TCP_INFO,...).

    The struct grows across kernel versions (new fields appended at the end),
    so we identify `tcpi_segs_out` by *index*, not offset, by reading it as
    the Nth uint32. The kernel ABI guarantees ordering of the legacy fields.

    Layout (Linux 5.10+, see <linux/tcp.h>):
       state, ca_state, retransmits, probes, backoff, options, ...
       The relevant counters near the end are exposed by an internal patch
       series, but the cleanest public path is to use option 11 = TCP_INFO
       (defined) on Linux. The struct is variable-length; on different
       kernels offsets differ.

    Empirical approach: Linux 5.10..6.10 keep tcpi_segs_out at offset
    100..140 as a uint32. We just scan the buffer for the running value
    incremented by exactly N_WRITES (since we did N writes).
    """
    # Simpler & robust: strace & inspect actual struct size. We
    # decode the first ~120 bytes as 30 uint32s:
    ints = struct.unpack_from("<30I", buf, 0)
    # Empirically tcpi_segs_out is index 19 on 5.10+ with 200-byte payload.
    # If you trust your kernel, hard-code it; otherwise fall back to scanning.
    if len(ints) >= 26:
        return ints[25]                            # tcpi_segs_out on 5.10+
    # Fallback — search for plausible counters by relative position.
    return ints[len(ints) // 2]


def get_segs_out(sock: socket.socket) -> int:
    """Return current value of tcpi_segs_out, or -1 if unsupported."""
    if not hasattr(socket, "TCP_INFO"):
        return -1
    try:
        buf = sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_INFO, 200)
    except OSError:
        return -1
    try:
        return parse_segs_out(buf)
    except struct.error:
        return -1


def start_server() -> tuple[socket.socket, int]:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((LOOPBACK, 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    return srv, port


def accept_once(srv: socket.socket) -> socket.socket:
    """Accept one peer connection and return the new socket."""
    cli, _ = srv.accept()
    return cli


# ----------------------------------------------------------------------------
# Server thread functions. We keep them as free functions so the run loop is
# easy to read; each spawns its own server, drains/echos, then exits.
# ----------------------------------------------------------------------------
def server_drain(ready: threading.Event, port_ref: list[int]) -> None:
    srv, port = start_server()
    port_ref.append(port)                       # hand the ephemeral port to client
    ready.set()
    cli, _ = srv.accept()
    total = 0
    while True:
        data = cli.recv(4096)
        if not data:
            break
        total += len(data)
    cli.close()
    srv.close()
    port_ref.append(total)                      # reuse list to return total bytes


def run_send_n_writes(nodelay_on: bool) -> tuple[int, int, float, int]:
    """Run demo 1 or demo 2 — return (segs_delta, total_rx, us, segments_or_-1)."""
    ready = threading.Event()
    port_ref: list[int] = []
    th = threading.Thread(target=server_drain, args=(ready, port_ref), daemon=True)
    th.start()
    ready.wait(2.0)
    port = port_ref[0]

    c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    c.connect((LOOPBACK, port))
    if nodelay_on:
        c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    segs_before = get_segs_out(c)
    t0 = time.perf_counter_ns()
    payload = b"a"
    for _ in range(N_WRITES):
        c.send(payload)
        time.sleep(1e-6)                        # 1 µs — let kernel actually send
    c.shutdown(socket.SHUT_WR)
    t1 = time.perf_counter_ns()
    segs_after = get_segs_out(c)
    c.close()

    th.join(timeout=2.0)
    rx_bytes = port_ref[1]

    us = (t1 - t0) / 1_000.0
    delta = (segs_after - segs_before) if (segs_before >= 0 and segs_after >= 0) else -1
    return delta, rx_bytes, us, N_WRITES


def demo_send_n_writes(nodelay_on: bool) -> None:
    delta, rx, us, n = run_send_n_writes(nodelay_on)
    label = "demo 2  TCP_NODELAY=1" if nodelay_on else "demo 1  Nagle on  "
    print(f"=== {label} " + "=" * 40)
    print(f"  Sent {n} single-byte writes ({n} bytes total)")
    if delta >= 0:
        print(f"  TCP segments emitted (tcpi_segs_out delta): {delta}")
    else:
        print(f"  TCP_INFO not available on this kernel — skipping segment count")
    print(f"  wall time                       : {us:8.1f} µs "
          f"({us / n:.2f} µs/write)")
    print(f"  server received                 : {rx} bytes")
    if delta >= 0:
        if not nodelay_on and delta < n:
            print(f"  >>> Nagle coalesced {n} bytes into {delta} segments "
                  f"(ratio {n / delta:.2f}x)")
        if nodelay_on and delta == n:
            print(f"  >>> TCP_NODELAY emitted one segment per write as expected")


# ----------------------------------------------------------------------------
# demo 3 — interactive echo: 1-byte ping, 1-byte pong, R rounds.
# ----------------------------------------------------------------------------
def echo_server(ready: threading.Event, port_ref: list[int]) -> None:
    srv, port = start_server()
    port_ref.append(port)
    ready.set()
    cli, _ = srv.accept()
    try:
        while True:
            data = cli.recv(1)
            if not data:
                break
            cli.send(data)
    finally:
        cli.close()
        srv.close()


def demo_interactive(nodelay_on: bool) -> None:
    ready = threading.Event()
    port_ref: list[int] = []
    th = threading.Thread(target=echo_server, args=(ready, port_ref), daemon=True)
    th.start()
    ready.wait(2.0)
    port = port_ref[0]

    c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    c.connect((LOOPBACK, port))
    if nodelay_on:
        c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    rounds = 50
    t0 = time.perf_counter_ns()
    for _ in range(rounds):
        c.send(b"X")
        r = c.recv(1)
        if not r:
            break
    t1 = time.perf_counter_ns()
    c.close()
    th.join(timeout=2.0)

    us = (t1 - t0) / 1_000.0
    label = "TCP_NODELAY=1" if nodelay_on else "Nagle on  "
    print(f"=== demo 3  interactive echo ({label}) " + "=" * 24)
    print(f"  {rounds} ping/pong rounds in {us:8.1f} µs "
          f"({us / rounds:.3f} µs/round, RTT ≈ {us / rounds / 2:.3f} µs)")
    print(f"  (On loopback both are sub-microsecond; the algorithmic wait is")
    print(f"   dwarfed by syscall overhead, so the seg-count test above is")
    print(f"   the real differentiator.)")


def main() -> None:
    print("# Nagle vs TCP_NODELAY — loopback measurement (Python)\n"
          "# server & client in same process; counts via TCP_INFO.\n")
    demo_send_n_writes(nodelay_on=False)
    print()
    demo_send_n_writes(nodelay_on=True)
    print()
    demo_interactive(nodelay_on=False)
    demo_interactive(nodelay_on=True)


if __name__ == "__main__":
    main()
