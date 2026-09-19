# -*- coding: utf-8 -*-
"""Idempotency-Key 自检。运行：python check.py"""

import sys

import idempotency as I

FAIL = 0
COUNT = 0


def ck(label, cond):
    global FAIL, COUNT
    COUNT += 1
    if not cond:
        FAIL += 1
        print("FAIL: %s" % label)


K1 = "8e03978e-40d5-43e8-bc93-6894a57f9324"
K2 = "clkyoesmbgybucifusbbtdsbohtyuuwz"


class OrderService:
    """假业务：每次真正执行就创建一个新订单（订单号自增）。"""

    def __init__(self) -> None:
        self.seq = 0
        self.created: list = []

    def create(self, payload):
        def handler():
            self.seq += 1
            order = {"id": self.seq, "item": payload["item"]}
            self.created.append(order)
            return 201, order
        return handler


# --- §2.1 结构化字段 String -------------------------------------------------
ck("带引号才是合法的 Structured Header String",
   I.parse_structured_string('"%s"' % K1) == K1)
ck("不带引号 → 解析失败", I.parse_structured_string(K1) is None)
ck("缺头返回 None", I.parse_structured_string(None) is None)

# --- 幂等方法不需要键 -------------------------------------------------------
svc = OrderService()
mw = I.IdempotencyMiddleware()
r = mw.handle("PUT", "c1", {}, {"item": 1}, svc.create({"item": 1}))
ck("PUT 不需要 Idempotency-Key", r.status == 201 and r.note == "method already idempotent")

# --- 无键（不强制）→ 重复创建，N 次就是 N 个订单 -----------------------------
svc = OrderService()
mw = I.IdempotencyMiddleware(require_key=False)
mw.handle("POST", "c1", {}, {"item": 1}, svc.create({"item": 1}))
mw.handle("POST", "c1", {}, {"item": 1}, svc.create({"item": 1}))
ck("不要求键时两次 POST 创建两个订单", svc.seq == 2)

# --- §2.6 首次 / 重试 / 并发 -------------------------------------------------
svc = OrderService()
mw = I.IdempotencyMiddleware()
h = {"Idempotency-Key": '"%s"' % K1}
r1 = mw.handle("POST", "c1", h, {"item": 1}, svc.create({"item": 1}))
ck("首次请求正常处理（201）", r1.status == 201 and r1.note == "first time")
r2 = mw.handle("POST", "c1", h, {"item": 1}, svc.create({"item": 1}))
ck("重试返回先前结果（同 body）", r2.note == "replayed" and r2.body == r1.body)
ck("重试不会真的再创建一次", svc.seq == 1)
ck("重试的状态码与原结果一致", r2.status == 201)

# 并发：手工把记录拨回 in_progress
rec = mw.store[("c1", K1)]
rec.state = "in_progress"
r3 = mw.handle("POST", "c1", h, {"item": 1}, svc.create({"item": 1}))
ck("原请求未完成时的重试 → 409", r3.status == 409 and r3.note == "concurrent")
ck("409 不需要修正请求即可重试（§2.7 例外）", r3.body["title"].startswith("A request is outstanding"))
rec.state = "done"

# --- §2.7 缺头 / 键复用不同 payload -----------------------------------------
r = mw.handle("POST", "c1", {}, {"item": 1}, svc.create({"item": 1}))
ck("缺 Idempotency-Key → 400", r.status == 400 and r.note == "missing key")
ck("400 响应带文档链接（Link 头）", "describedby" in r.headers["Link"])
ck("400 用 problem+json 描述", r.body["type"] == I.PROBLEM_TYPE)

r = mw.handle("POST", "c1", h, {"item": 2}, svc.create({"item": 2}))
ck("同键不同 payload → 422", r.status == 422 and r.note == "fingerprint mismatch")
ck("422 时后端一次都没被调用", svc.seq == 1)

# 换个键就是新的一次
r = mw.handle("POST", "c1", {"Idempotency-Key": '"%s"' % K2},
              {"item": 2}, svc.create({"item": 2}))
ck("换键后是首次请求", r.status == 201 and r.note == "first time")
ck("新键创建了第二个订单", svc.seq == 2)

# --- 格式与指纹 -------------------------------------------------------------
ck("UUID 键合规（§2.2 RECOMMENDED）", I.is_valid_key(K1))
ck("32 位随机串键合规（§6 示例）", I.is_valid_key(K2))
ck("短键不合规", not I.is_valid_key("abc"))
ck("带大写/非 UUID 形态不合规", not I.is_valid_key("NOT-A-UUID"))
r = mw.handle("POST", "c1", {"Idempotency-Key": '"abc"'},
              {"item": 3}, svc.create({"item": 3}))
ck("不合规键 → 400（处理前校验）", r.status == 400 and r.note == "format rejected")
r = mw.handle("POST", "c1", {"Idempotency-Key": K1}, {"item": 3}, svc.create({"item": 3}))
ck("非结构化字段 String → 400", r.status == 400 and r.note == "not a structured string")

ck("fingerprint 对 payload 敏感",
   I.fingerprint({"item": 1}) != I.fingerprint({"item": 2}))
ck("fingerprint 与键顺序无关",
   I.fingerprint({"a": 1, "b": 2}) == I.fingerprint({"b": 2, "a": 1}))

# --- §5 复合键：不同客户端互不可见 -------------------------------------------
svc2 = OrderService()
mw2 = I.IdempotencyMiddleware()
r = mw2.handle("POST", "c1", h, {"item": 9}, svc2.create({"item": 9}))
ck("客户端 c1 首次成功", r.status == 201)
r = mw2.handle("POST", "c2", h, {"item": 9}, svc2.create({"item": 9}))
ck("客户端 c2 用同一个键 → 视为首次（复合键隔离）",
   r.note == "first time" and svc2.seq == 2)
ck("两个客户端拿到不同的订单号", r.body["id"] == 2)

# --- §2.3 过期策略 ----------------------------------------------------------
mw3 = I.IdempotencyMiddleware(ttl=10.0)
svc3 = OrderService()
mw3.clock = 100.0
mw3.handle("POST", "c1", h, {"item": 1}, svc3.create({"item": 1}))
mw3.clock = 105.0
ck("未过期时不清理", mw3.purge_expired() == 0)
mw3.clock = 200.0
ck("过期后被清理", mw3.purge_expired() == 1)
r = mw3.handle("POST", "c1", h, {"item": 1}, svc3.create({"item": 1}))
ck("过期后同一键被视为新请求", r.note == "first time" and svc3.seq == 2)

# --- 错误结果也会被原样重放（§2.6 "success or an error"）---------------------
mw4 = I.IdempotencyMiddleware()
def failing():
    return 500, I.problem(500, "Internal Server Error", "boom")
r1 = mw4.handle("POST", "c1", h, {"item": 1}, failing)
r2 = mw4.handle("POST", "c1", h, {"item": 1}, failing)
ck("失败结果也被记录", r1.status == 500)
ck("失败结果被原样重放", r2.status == 500 and r2.note == "replayed")
ck("重放的是同一个 body", r2.body == r1.body)

print("assertions=%d fail=%d" % (COUNT, FAIL))
sys.exit(1 if FAIL else 0)
