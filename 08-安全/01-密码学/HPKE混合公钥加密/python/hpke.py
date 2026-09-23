"""HPKE（RFC 9180）：DHKEM + 标签化 KDF + 加密上下文。

只实现附录 A.1 那套：`DHKEM(X25519, HKDF-SHA256)` / `HKDF-SHA256` / `AES-128-GCM`。
"""

import hpke_aes
import hpke_x25519 as X

KEM_ID, KDF_ID, AEAD_ID = 0x0020, 0x0001, 0x0001
KEM_SUITE_ID = b"KEM" + KEM_ID.to_bytes(2, "big")
HPKE_SUITE_ID = b"HPKE" + KEM_ID.to_bytes(2, "big") + KDF_ID.to_bytes(2, "big") \
    + AEAD_ID.to_bytes(2, "big")
HPKE_V1 = b"HPKE-v1"

Nk, Nn, Nt = 16, 12, 16
Nh = 32
MODE_BASE, MODE_PSK, MODE_AUTH, MODE_AUTH_PSK = 0x00, 0x01, 0x02, 0x03
DEFAULT_PSK = b""
DEFAULT_PSK_ID = b""


class ValidationError(Exception):
    pass


class MessageLimitReachedError(Exception):
    pass


class OpenError(Exception):
    pass


# ------------------------------------------------------------- §4 标签化
def labeled_extract(salt, label, ikm, suite_id):
    return X.hkdf_extract(salt, HPKE_V1 + suite_id + label + ikm)


def labeled_expand(prk, label, info, length, suite_id):
    return X.hkdf_expand(prk, length.to_bytes(2, "big") + HPKE_V1 + suite_id
                         + label + info, length)


# --------------------------------------------------------- §4.1 DHKEM
def extract_and_expand(dh, kem_context):
    eae_prk = labeled_extract(b"", b"eae_prk", dh, KEM_SUITE_ID)
    return labeled_expand(eae_prk, b"shared_secret", kem_context, Nh, KEM_SUITE_ID)


def encap(pk_r, sk_e=None):
    """sk_e 可注入，便于用 RFC 的固定向量对拍。"""
    if sk_e is None:
        import os
        sk_e = os.urandom(32)
    pk_e = X.x25519_base(sk_e)
    dh = X.x25519(sk_e, pk_r)
    return extract_and_expand(dh, pk_e + pk_r), pk_e


def decap(enc, sk_r):
    pk_r = X.x25519_base(sk_r)
    dh = X.x25519(sk_r, enc)
    return extract_and_expand(dh, enc + pk_r)


# ------------------------------------------------------- §5.1 KeySchedule
def verify_psk_inputs(mode, psk, psk_id):
    """§5.1 —— 判据是「是否等于 default_psk / default_psk_id（都是空串）」，
    不是「是否为 None」。写成 None 判据会让默认参数看起来像「提供了 PSK」。
    """
    got_psk = psk != DEFAULT_PSK
    got_psk_id = psk_id != DEFAULT_PSK_ID
    if got_psk != got_psk_id:
        raise ValidationError("inconsistent PSK inputs")
    if got_psk and mode in (MODE_BASE, MODE_AUTH):
        raise ValidationError("PSK input provided when not needed")
    if (not got_psk) and mode in (MODE_PSK, MODE_AUTH_PSK):
        raise ValidationError("PSK required but not provided")


def key_schedule(mode, shared_secret, info, psk=b"", psk_id=b""):
    verify_psk_inputs(mode, psk, psk_id)
    psk_id_hash = labeled_extract(b"", b"psk_id_hash", psk_id, HPKE_SUITE_ID)
    info_hash = labeled_extract(b"", b"info_hash", info, HPKE_SUITE_ID)
    ksc = bytes([mode]) + psk_id_hash + info_hash
    secret = labeled_extract(shared_secret, b"secret", psk, HPKE_SUITE_ID)
    key = labeled_expand(secret, b"key", ksc, Nk, HPKE_SUITE_ID)
    base_nonce = labeled_expand(secret, b"base_nonce", ksc, Nn, HPKE_SUITE_ID)
    exporter_secret = labeled_expand(secret, b"exp", ksc, Nh, HPKE_SUITE_ID)
    return {
        "key": key,
        "base_nonce": base_nonce,
        "seq": 0,
        "exporter_secret": exporter_secret,
        "key_schedule_context": ksc,
        "secret": secret,
    }


# --------------------------------------------------- §5.2 / §5.3 上下文
class Context:
    def __init__(self, key, base_nonce, exporter_secret, role):
        self.aead = hpke_aes.AES128GCM(key)
        self.base_nonce = base_nonce
        self.exporter_secret = exporter_secret
        self.seq = 0
        self.role = role

    def compute_nonce(self, seq):
        sb = seq.to_bytes(Nn, "big")
        return bytes(a ^ b for a, b in zip(self.base_nonce, sb))

    def increment_seq(self):
        if self.seq >= (1 << (8 * Nn)) - 1:
            raise MessageLimitReachedError()
        self.seq += 1

    def seal(self, aad, pt):
        if self.role != "S":
            raise ValidationError("sender context only")
        ct = self.aead.seal(self.compute_nonce(self.seq), aad, pt)
        self.increment_seq()
        return ct

    def open(self, aad, ct):
        if self.role != "R":
            raise ValidationError("recipient context only")
        try:
            pt = self.aead.open(self.compute_nonce(self.seq), aad, ct)
        except ValueError:
            raise OpenError("authentication failed")
        self.increment_seq()
        return pt

    def export(self, exporter_context, length):
        return labeled_expand(self.exporter_secret, b"sec", exporter_context,
                              length, HPKE_SUITE_ID)


def setup_base_s(pk_r, info, sk_e=None):
    ss, enc = encap(pk_r, sk_e)
    return enc, ContextS(key_schedule(MODE_BASE, ss, info))


def setup_base_r(enc, sk_r, info):
    ss = decap(enc, sk_r)
    return ContextR(key_schedule(MODE_BASE, ss, info))


def ContextS(ks):
    return Context(ks["key"], ks["base_nonce"], ks["exporter_secret"], "S")


def ContextR(ks):
    return Context(ks["key"], ks["base_nonce"], ks["exporter_secret"], "R")
