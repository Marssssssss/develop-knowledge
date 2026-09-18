"""
Dragonfly 的提交/确认交换（RFC 7664 §3.3 / §3.4）

提交交换（Commit）：
    scalar  = (private + mask) mod q
    Element = inverse(scalar-op(mask, PE))          # = PE^(-mask) mod p
    ss      = scalar-op(private, element-op(peer-Element, scalar-op(peer-scalar, PE)))
    kck | mk = KDF(ss, "Dragonfly Key Derivation")，长度 2*len(p)

把 peer 的式子代进去看：peer-Element * PE^peer-scalar = PE^(-mask') * PE^(priv'+mask') = PE^priv'，
再取 private 次幂就得到 PE^(priv*priv') —— 双方对称，这就是共享密钥的来源。

必须守住的三条（规范里的 MUST）：
  1. 生成后 scalar < 2 或 1 < private/mask < q 不成立 → 扔掉重来
  2. mask 用完**必须**立即销毁（它是唯一能把 Element 反推回 PE 的东西）
  3. 对端若返回与自己**完全相同**的 scalar 与 Element，是反射攻击，必须中止

本文件不实现「反拥塞 / 防 DoS」（WPA3 用 Anti-Clogging Token），那属于接入协议的范畴。
"""

from __future__ import annotations

import secrets

from sae_group import (KEY_LABEL, P, P_BYTES, Q, element_op, h, inverse, kdf,
                       is_valid_element, scalar_op, derive_pwe)

Q_BYTES = (Q.bit_length() + 7) // 8


class SaeError(Exception):
    """对端发来的标量/元素不合法，或检测到反射攻击"""


class SaePeer:
    """一端的 SAE 状态机。rng(n) 需返回 [0, n) 内的整数，默认用 secrets。"""

    def __init__(self, name: bytes, password: bytes, peer_name: bytes, k: int = 40,
                 rng=None, nonce: bytes = b"") -> None:
        self.name = name
        self.peer_name = peer_name
        self.k = k
        self.rng = rng if rng is not None else secrets.randbelow
        self.pe = derive_pwe(password, name, peer_name, k, nonce)
        self.private = self.mask = None
        self.scalar = self.element = None
        self.peer_scalar = self.peer_element = None
        self.ss = self.kck = self.mk = None

    # -------------------------------------------------- 提交交换

    def _rand_scalar(self) -> int:
        """1 < x < q（规范对 private 与 mask 的要求）"""
        return 2 + self.rng(Q - 3)

    def commit(self) -> tuple[int, int]:
        """生成并返回 (scalar, Element)，随后销毁 mask。"""
        while True:
            self.private = self._rand_scalar()
            self.mask = self._rand_scalar()
            self.scalar = (self.private + self.mask) % Q
            if self.scalar >= 2:
                break
        self.element = inverse(scalar_op(self.mask, self.pe))
        self.mask = None                       # MUST：mask 用完立即销毁
        return self.scalar, self.element

    def absorb(self, peer_scalar: int, peer_element: int) -> None:
        """校验并吸收对端的提交，随后推导 ss / kck / mk。"""
        if self.scalar is None:
            raise SaeError("必须先 commit 再吸收对端提交")
        if peer_scalar == self.scalar and peer_element == self.element:
            raise SaeError("对端返回了与自己相同的标量与元素：反射攻击")
        if not 1 < peer_scalar < Q:
            raise SaeError("对端标量不在 (1, q) 内")
        if not is_valid_element(peer_element):
            raise SaeError("对端元素不是合法的子群元素")
        self.peer_scalar, self.peer_element = peer_scalar, peer_element
        inner = element_op(peer_element, scalar_op(peer_scalar, self.pe))
        self.ss = scalar_op(self.private, inner)
        material = kdf(KEY_LABEL, self._ss_bytes(), 2 * P.bit_length())
        self.kck = material >> (P.bit_length())        # 前一半是 kck
        self.mk = material & ((1 << P.bit_length()) - 1)

    def _ss_bytes(self) -> bytes:
        """ss 是群元素，按 p 的字节宽度定长序列化 —— 变长会让 KDF 输入有歧义。"""
        return self.ss.to_bytes(P_BYTES, "big")

    # -------------------------------------------------- 确认交换

    def confirm(self) -> bytes:
        """自己要发出的确认值（发送方视角：自己的标量/元素在前）。"""
        return self._confirm_value(self.scalar, self.peer_scalar, self.element,
                                   self.peer_element, self.name)

    def expected_peer_confirm(self) -> bytes:
        """从对端**应该**收到的确认值。

        注意顺序是「发送方在前、接收方在后」（RFC 7664 §3.4），而这里的发送方是**对端**，
        所以标量/元素的先后与自己 confirm() 时正好相反 —— 把 verify() 写成
        「与自己 confirm() 比较」是最容易犯的错。
        """
        return self._confirm_value(self.peer_scalar, self.scalar, self.peer_element,
                                   self.element, self.peer_name)

    def _confirm_value(self, scalar: int, peer_scalar: int, element: int,
                       peer_element: int, sender: bytes) -> bytes:
        """confirm = H(kck | 发送方标量 | 接收方标量 | 发送方元素 | 接收方元素 | 发送方标识)

        四个数值都按定宽大端序列化：变长拼接会让不同的 (标量, 元素) 组合撞出同一个输入串。
        """
        if self.kck is None:
            raise SaeError("还没有共享密钥，无法生成确认值")
        return h(self.kck.to_bytes(P_BYTES, "big")
                 + scalar.to_bytes(Q_BYTES, "big")
                 + peer_scalar.to_bytes(Q_BYTES, "big")
                 + element.to_bytes(P_BYTES, "big")
                 + peer_element.to_bytes(P_BYTES, "big")
                 + sender)

    def verify(self, peer_confirm: bytes) -> bool:
        return self.kck is not None and \
            secrets.compare_digest(peer_confirm, self.expected_peer_confirm())


def run_exchange(pw_a: bytes, pw_b: bytes, id_a: bytes = b"alice",
                 id_b: bytes = b"bob", k: int = 40, rng=None) -> tuple[SaePeer, SaePeer]:
    """跑一次完整交换，返回两端的最终状态（供自检与对照实验使用）。"""
    a = SaePeer(id_a, pw_a, id_b, k, rng)
    b = SaePeer(id_b, pw_b, id_a, k, rng)
    sa, ea = a.commit()
    sb, eb = b.commit()
    a.absorb(sb, eb)
    b.absorb(sa, ea)
    return a, b
