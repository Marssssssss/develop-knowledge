"""Terraform state locking simulation (S3 lockfile + DynamoDB LockID semantics).

Refs read before writing this file:
  - HashiCorp "State: Locking" https://developer.hashicorp.com/terraform/language/state/locking
  - HashiCorp "Backend Type: s3" https://developer.hashicorp.com/terraform/language/backend/s3

Key facts mirrored here:
  * Locking happens automatically on every operation that can write state; if the
    lock cannot be acquired Terraform does NOT continue.
  * `force-unlock` requires the lock ID, which acts as a nonce (Wikipedia: nonce).
  * The S3 backend stores the default workspace at `<key>` and every other
    workspace at `<workspace_key_prefix>/<workspace_name>/<key>` (default prefix
    `env:`).
  * `use_lockfile = true` puts the lock at `<key>.tflock` and needs
    s3:GetObject / s3:PutObject / s3:DeleteObject on that object.
  * The (deprecated) DynamoDB table needs a partition key named `LockID` of type
    String; the lock is a conditional PutItem on that key.
"""

from __future__ import annotations

import json
import os
import socket
import time
import uuid
from dataclasses import dataclass, field


class LockError(Exception):
    """Raised when a lock cannot be acquired (mirrors Terraform's refusal)."""


@dataclass
class LockInfo:
    """Payload Terraform shows when it fails to acquire a lock."""

    id: str
    path: str
    operation: str
    who: str
    version: str
    created: str

    def render(self) -> str:
        return (
            "\nError: Error acquiring the state lock\n"
            "Error message: ConditionalCheckFailedException: the lock is held\n"
            "Lock Info:\n"
            f"  ID:        {self.id}\n"
            f"  Path:      {self.path}\n"
            f"  Operation: {self.operation}\n"
            f"  Who:       {self.who}\n"
            f"  Version:   {self.version}\n"
            f"  Created:   {self.created}\n"
        )


@dataclass
class ObjectStore:
    """Minimal S3-compatible store with *conditional* writes.

    S3 conditional writes (`If-None-Match: *`) and DynamoDB's
    `attribute_not_exists(LockID)` are the same primitive: create-only.
    """

    objects: dict[str, str] = field(default_factory=dict)

    def get(self, key: str) -> str | None:
        return self.objects.get(key)

    def put(self, key: str, body: str) -> None:
        self.objects[key] = body

    def put_if_absent(self, key: str, body: str) -> bool:
        """Return True when written, False when the key already exists."""
        if key in self.objects:
            return False
        self.objects[key] = body
        return True

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)


class S3Backend:
    """Path layout of the S3 backend (state object + optional .tflock object)."""

    def __init__(self, bucket: str, key: str, workspace_key_prefix: str = "env:"):
        self.bucket = bucket
        self.key = key
        self.workspace_key_prefix = workspace_key_prefix

    def state_key(self, workspace: str = "default") -> str:
        # Documented: default workspace -> <key>; others -> prefix/name/key.
        if workspace == "default":
            return self.key
        return f"{self.workspace_key_prefix}/{workspace}/{self.key}"

    def lock_key(self, workspace: str = "default") -> str:
        return self.state_key(workspace) + ".tflock"


class LockManager:
    """Acquire/release a state lock with a nonce-style lock ID."""

    def __init__(self, store: ObjectStore, backend: S3Backend, version: str = "1.16.0"):
        self.store = store
        self.backend = backend
        self.version = version

    def _who(self) -> str:
        return f"{socket.gethostname()}@{os.getpid()}"

    def acquire(self, operation: str, workspace: str = "default") -> str:
        key = self.backend.lock_key(workspace)
        lock_id = str(uuid.uuid4())
        # NOTE: the nonce is generated *before* the conditional write, so the
        # holder and the reported ID always agree.
        payload = json.dumps(
            {
                "ID": lock_id,
                "Operation": operation,
                "Who": self._who(),
                "Version": self.version,
                "Created": time.strftime("%Y-%m-%d %H:%M:%S %z"),
                "Path": f"{self.backend.bucket}/{key}",
            }
        )
        if not self.store.put_if_absent(key, payload):
            existing = json.loads(self.store.get(key) or "{}")
            raise LockError(
                LockInfo(
                    id=existing.get("ID", "(none)"),
                    path=existing.get("Path", key),
                    operation=existing.get("Operation", "unknown"),
                    who=existing.get("Who", "unknown"),
                    version=existing.get("Version", "unknown"),
                    created=existing.get("Created", "unknown"),
                ).render()
            )
        return lock_id

    def release(self, lock_id: str, workspace: str = "default") -> bool:
        """Release only if we still own the lock (compare-and-delete)."""
        key = self.backend.lock_key(workspace)
        raw = self.store.get(key)
        if raw is None:
            return False
        if json.loads(raw).get("ID") != lock_id:
            return False  # somebody else's lock, or it was force-unlocked
        self.store.delete(key)
        return True

    def force_unlock(self, lock_id: str, workspace: str = "default") -> bool:
        """`terraform force-unlock <ID>`: the ID must match, it is the guard."""
        key = self.backend.lock_key(workspace)
        raw = self.store.get(key)
        if raw is None:
            return False
        if json.loads(raw).get("ID") != lock_id:
            raise LockError(
                f"Invalid lock id '{lock_id}'. The lock ID does not match the "
                f"current lock holder, refusing to unlock."
            )
        self.store.delete(key)
        return True

    def is_stale(self, max_age_seconds: float, workspace: str = "default") -> bool:
        """Heuristic for a crashed holder (Terraform itself has no TTL)."""
        raw = self.store.get(self.backend.lock_key(workspace))
        if raw is None:
            return False
        created = json.loads(raw).get("Created", "")
        try:
            ts = time.mktime(time.strptime(created[:19], "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            return True
        return (time.time() - ts) > max_age_seconds


def _demo() -> None:
    store = ObjectStore()
    backend = S3Backend("tf-state-prod", "path/to/my/key")
    print("state object :", backend.state_key())
    print("prod state   :", backend.state_key("production"))
    print("lock object  :", backend.lock_key())
    print("dynamodb key : LockID (String) -- conditional PutItem\n")

    alice = LockManager(store, backend)
    bob = LockManager(store, backend)

    lock_id = alice.acquire("plan")
    print("[1] alice acquires  ->", lock_id)

    try:
        bob.acquire("apply")
    except LockError as err:
        print("[2] bob is refused:")
        for line in str(err).strip().splitlines():
            print("        ", line)
        print("    (terraform stops here unless --lock=false is passed)")

    print("[3] alice releases  ->", alice.release(lock_id))
    print("[3] bob releases    ->", bob.release(lock_id), "(not the owner)")

    lock_id = alice.acquire("apply", workspace="production")
    print("[4] prod lock holds :", store.get(backend.lock_key("production")) is not None)
    try:
        bob.force_unlock(str(uuid.uuid4()), workspace="production")
    except LockError as err:
        print("[5] wrong nonce     ->", str(err)[:60], "...")
    print("[5] right nonce     ->", bob.force_unlock(lock_id, workspace="production"))

    # A crashed holder leaves the lock behind forever: Terraform has no TTL,
    # so automation must sweep by age -- exactly what `force-unlock` is for.
    # Rewrite only the Created field to look like a lock from an hour ago.
    stale = LockManager(store, backend)
    stale_id = stale.acquire("destroy")
    dead = json.loads(store.get(backend.lock_key()) or "{}")
    dead["Created"] = time.strftime(
        "%Y-%m-%d %H:%M:%S %z", time.localtime(time.time() - 3600)
    )
    store.put(backend.lock_key(), json.dumps(dead))
    print("[6] stale detected  ->", stale.is_stale(60))
    print("[6] sweep by nonce  ->", stale.force_unlock(stale_id))
    print("[6] lock removed    ->", store.get(backend.lock_key()) is None)


if __name__ == "__main__":
    _demo()
