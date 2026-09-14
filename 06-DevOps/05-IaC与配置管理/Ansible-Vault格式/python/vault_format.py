"""Ansible Vault payload format: armor, key derivation, HMAC, AES-256-CTR.

Refs read before writing this file:
  - Ansible docs, "Using encrypted variables and files" -> "Format of files
    encrypted with Ansible Vault" / "Ansible Vault payload format 1.1 - 1.2"
    https://docs.ansible.com/projects/ansible-core/devel/vault_guide/vault_using_encrypted_content.html
  - RFC 2104 (HMAC), RFC 5652 §6.3 (padding), NIST SP 800-38A (CTR mode)

The documented format, quoted and then implemented literally:

  * Header: `$ANSIBLE_VAULT;1.1;AES256` or `$ANSIBLE_VAULT;1.2;AES256;<vault-id>`
    -- up to four `;`-separated fields; 1.0 is read-only and rewritten as 1.1.
  * Everything after the header is the "vaulttext": a text-armored version of
    the ciphertext, "Each line is 80 characters wide, except for the last line".
  * The vaulttext is the hexlify()'ed result of, in order:
        hexlify(salt) + newline + hexlify(hmac) + newline + hexlify(ciphertext)
  * Keys come from PBKDF2(salt, 10000 iterations, SHA256) expanded to 80 bytes:
    the first 32 are the cipher key, the next 32 the HMAC key, the last 16 the
    cipher IV.
  * The HMAC is RFC 2104 style over the AES256-encrypted ciphertext.
  * Encryption is AES-CTR with the 128-bit counter block seeded from the IV;
    the plaintext is padded to the AES block size (RFC 5652 padding).
"""

from __future__ import annotations

import binascii
import hashlib
import hmac
import os

from aes import AES256, ctr_keystream, xor_bytes

HEADER = "$ANSIBLE_VAULT"
LINE_WIDTH = 80
PBKDF2_ITERATIONS = 10000
DERIVED_LENGTH = 80  # 32 cipher key + 32 HMAC key + 16 IV


class VaultError(Exception):
    """Raised when the payload is malformed or fails authentication."""


class VaultFormat:
    """Reader/writer for the documented Vault payload layout."""

    def __init__(self, password: bytes):
        self.password = password

    # -- key derivation ----------------------------------------------------

    def _derive(self, salt: bytes) -> tuple[bytes, bytes, bytes]:
        dk = hashlib.pbkdf2_hmac("sha256", self.password, salt,
                                 PBKDF2_ITERATIONS, dklen=DERIVED_LENGTH)
        return dk[:32], dk[32:64], dk[64:80]

    # -- armor / unarmor ---------------------------------------------------

    @staticmethod
    def _armor(payload: str) -> str:
        """hexlify, then hard-wrap to 80 columns (documented line width)."""
        text = binascii.hexlify(payload.encode("utf-8")).decode("ascii")
        return "\n".join(text[i:i + LINE_WIDTH]
                         for i in range(0, len(text), LINE_WIDTH))

    @staticmethod
    def _unarmor(lines: list[str]) -> str:
        joined = "".join(line.strip() for line in lines if line.strip())
        try:
            return binascii.unhexlify(joined).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise VaultError(f"vaulttext is not valid hex: {exc}") from exc

    # -- encrypt / decrypt -------------------------------------------------

    def encrypt(self, plaintext: str, vault_id: str | None = None) -> str:
        salt = os.urandom(32)
        cipher_key, hmac_key, iv = self._derive(salt)
        padded = self._pkcs7_pad(plaintext.encode("utf-8"))
        stream = ctr_keystream(AES256(cipher_key), int.from_bytes(iv, "big"), len(padded))
        ciphertext = xor_bytes(padded, stream)
        digest = hmac.new(hmac_key, ciphertext, hashlib.sha256).digest()

        body = (binascii.hexlify(salt).decode()
                + "\n" + binascii.hexlify(digest).decode()
                + "\n" + binascii.hexlify(ciphertext).decode())
        version = "1.2" if vault_id else "1.1"
        head = f"{HEADER};{version};AES256" + (f";{vault_id}" if vault_id else "")
        return head + "\n" + self._armor(body) + "\n"

    def decrypt(self, text: str) -> str:
        lines = text.splitlines()
        if not lines or not lines[0].startswith(HEADER + ";"):
            raise VaultError("not an Ansible Vault payload (bad header)")
        fields = lines[0].split(";")
        if len(fields) < 3:
            raise VaultError("truncated header")
        version, cipher_name = fields[1], fields[2]
        if cipher_name != "AES256":
            raise VaultError(f"unsupported cipher {cipher_name!r}")
        if version not in ("1.1", "1.2"):
            raise VaultError(f"unsupported vault format version {version!r}")

        parts = self._unarmor(lines[1:]).split("\n")
        if len(parts) != 3:
            raise VaultError(f"expected salt/hmac/ciphertext, got {len(parts)} parts")
        salt, digest, ciphertext = (binascii.unhexlify(p) for p in parts)

        cipher_key, hmac_key, iv = self._derive(salt)
        # Authenticate BEFORE decrypting: CTR is malleable, so the HMAC is the
        # only thing that tells a flipped bit from a legitimate edit.
        expected = hmac.new(hmac_key, ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, digest):
            raise VaultError("HMAC mismatch: wrong password or tampered ciphertext")

        plain = xor_bytes(ciphertext, ctr_keystream(AES256(cipher_key),
                                                    int.from_bytes(iv, "big"),
                                                    len(ciphertext)))
        return self._pkcs7_unpad(plain).decode("utf-8")

    @staticmethod
    def _pkcs7_pad(data: bytes) -> bytes:
        pad = 16 - (len(data) % 16)          # RFC 5652: always pad, 1..16 bytes
        return data + bytes([pad]) * pad

    @staticmethod
    def _pkcs7_unpad(data: bytes) -> bytes:
        if not data or len(data) % 16 != 0:
            raise VaultError("ciphertext length is not a multiple of the block size")
        pad = data[-1]
        if not 1 <= pad <= 16 or data[-pad:] != bytes([pad]) * pad:
            raise VaultError("invalid padding")
        return data[:-pad]


def _tamper(vault_text: str) -> str:
    """Flip one hex nibble inside the ciphertext part of the armor."""
    lines = vault_text.rstrip("\n").splitlines()
    idx = len(lines) - 1            # last line always holds ciphertext bytes
    if len(lines[idx].strip()) < 2:
        idx -= 1
    raw = lines[idx]
    pos = len(raw) - 1
    flipped = "0" if raw[pos] not in "0" else "1"
    lines[idx] = raw[:pos] + flipped
    return "\n".join(lines) + "\n"


def main() -> None:
    secret = "the_secret: hunter2"
    vault = VaultFormat(b"a_password_file")

    print("== key derivation (PBKDF2-HMAC-SHA256, 10000 iterations, 80 bytes) ==")
    salt = os.urandom(32)
    cipher_key, hmac_key, iv = vault._derive(salt)
    print(f"  salt {len(salt)}B | cipher key {len(cipher_key)}B "
          f"| HMAC key {len(hmac_key)}B | IV {len(iv)}B")
    print("  cipher key[:8] =", cipher_key[:8].hex())

    print("\n== encrypt (format 1.1, no vault id) ==")
    blob = vault.encrypt(secret)
    print("\n".join("  " + ln for ln in blob.rstrip("\n").splitlines()[:5]))
    print("  ... (%d armor lines total, width <= %d)"
          % (len(blob.rstrip('\n').splitlines()) - 1, LINE_WIDTH))

    print("\n== decrypt ==")
    print("  recovered:", repr(vault.decrypt(blob)))
    assert vault.decrypt(blob) == secret

    print("\n== tampered ciphertext (one hex nibble flipped) ==")
    try:
        vault.decrypt(_tamper(blob))
        print("  NOT DETECTED -- this must never happen")
    except VaultError as exc:
        print("  rejected:", exc)

    print("\n== wrong password ==")
    try:
        VaultFormat(b"not_the_password").decrypt(blob)
    except VaultError as exc:
        print("  rejected:", exc)

    print("\n== format 1.2 with a vault-id label ==")
    dev = vault.encrypt("the_dev_secret: foooodev", vault_id="dev")
    print("  header:", dev.splitlines()[0])
    print("  recovered:", repr(vault.decrypt(dev)))

    print("\n== randomness: identical input -> distinct payloads ==")
    blobs = {vault.encrypt(secret) for _ in range(4)}
    print(f"  {len(blobs)} distinct payloads from 4 encryptions of the same string")
    assert len(blobs) == 4, "salt must be fresh per encryption"

    print("\n== padding (RFC 5652) on an exact multiple of 16 ==")
    for n in (15, 16, 17):
        padded = VaultFormat._pkcs7_pad(b"x" * n)
        print(f"  {n:>2}B -> {len(padded):>2}B (pad={padded[-1]})")
    assert len(VaultFormat._pkcs7_pad(b"x" * 16)) == 32  # full extra block


if __name__ == "__main__":
    main()
