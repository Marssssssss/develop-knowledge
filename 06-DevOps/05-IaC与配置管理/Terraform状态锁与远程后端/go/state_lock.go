// Terraform state locking simulation (S3 lockfile + DynamoDB LockID semantics).
//
// Refs read before writing this file:
//   - HashiCorp "State: Locking"
//     https://developer.hashicorp.com/terraform/language/state/locking
//   - HashiCorp "Backend Type: s3"
//     https://developer.hashicorp.com/terraform/language/backend/s3
//
// Facts mirrored here:
//   - Locking is automatic for anything that can write state; on failure
//     Terraform does not continue. `-lock=false` disables it.
//   - `force-unlock` needs the lock ID, which acts as a nonce.
//   - S3 backend: default workspace -> <key>, others -> <prefix>/<ws>/<key>,
//     default prefix "env:".
//   - `use_lockfile = true` -> lock object at <key>.tflock.
//   - DynamoDB (deprecated) -> partition key `LockID`, type String.
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"time"
)

// Store is a minimal object store with conditional ("create-only") writes.
// S3's `If-None-Match: *` and DynamoDB's `attribute_not_exists(LockID)` are
// the same primitive, so one method models both.
type Store struct {
	Objects map[string]string
}

func NewStore() *Store { return &Store{Objects: map[string]string{}} }

func (s *Store) Get(key string) (string, bool) {
	v, ok := s.Objects[key]
	return v, ok
}

func (s *Store) Put(key, body string) { s.Objects[key] = body }

func (s *Store) PutIfAbsent(key, body string) bool {
	if _, exists := s.Objects[key]; exists {
		return false
	}
	s.Objects[key] = body
	return true
}

func (s *Store) Delete(key string) { delete(s.Objects, key) }

// Backend models the S3 backend's path layout.
type Backend struct {
	Bucket             string
	Key                string
	WorkspaceKeyPrefix string
}

func (b Backend) StateKey(workspace string) string {
	if workspace == "default" {
		return b.Key
	}
	return b.WorkspaceKeyPrefix + "/" + workspace + "/" + b.Key
}

func (b Backend) LockKey(workspace string) string {
	return b.StateKey(workspace) + ".tflock"
}

// LockBody is what Terraform writes into the lock object.
type LockBody struct {
	ID        string `json:"ID"`
	Operation string `json:"Operation"`
	Who       string `json:"Who"`
	Version   string `json:"Version"`
	Created   string `json:"Created"`
	Path      string `json:"Path"`
}

// Manager implements acquire / release / force-unlock.
type Manager struct {
	Store   *Store
	Backend Backend
	Version string
}

// Acquire writes the lock object with a create-only write. A false return
// carries the *existing* holder's info, which is what Terraform prints.
func (m *Manager) Acquire(operation, workspace string) (string, *LockBody, error) {
	key := m.Backend.LockKey(workspace)
	lockID := newNonce()
	host, _ := os.Hostname()
	body := LockBody{
		ID:        lockID,
		Operation: operation,
		Who:       fmt.Sprintf("%s@%d", host, os.Getpid()),
		Version:   m.Version,
		Created:   time.Now().Format("2006-01-02 15:04:05 -0700"),
		Path:      m.Backend.Bucket + "/" + key,
	}
	raw, _ := json.Marshal(body)
	if !m.Store.PutIfAbsent(key, string(raw)) {
		held, _ := m.Store.Get(key)
		var existing LockBody
		if err := json.Unmarshal([]byte(held), &existing); err != nil {
			return "", nil, err
		}
		return "", &existing, nil
	}
	return lockID, nil, nil
}

// Release is a compare-and-delete: only the owner may remove the lock.
func (m *Manager) Release(lockID, workspace string) bool {
	key := m.Backend.LockKey(workspace)
	held, ok := m.Store.Get(key)
	if !ok {
		return false
	}
	var existing LockBody
	if err := json.Unmarshal([]byte(held), &existing); err != nil {
		return false
	}
	if existing.ID != lockID {
		return false
	}
	m.Store.Delete(key)
	return true
}

// ForceUnlock models `terraform force-unlock <ID>`: the ID is the guard, a
// mismatching ID must be rejected instead of silently unlocking.
func (m *Manager) ForceUnlock(lockID, workspace string) (bool, error) {
	key := m.Backend.LockKey(workspace)
	held, ok := m.Store.Get(key)
	if !ok {
		return false, nil
	}
	var existing LockBody
	if err := json.Unmarshal([]byte(held), &existing); err != nil {
		return false, err
	}
	if existing.ID != lockID {
		return false, fmt.Errorf("invalid lock id %q: does not match the holder", lockID)
	}
	m.Store.Delete(key)
	return true, nil
}

// IsStale is a heuristic: Terraform has no lock TTL, so orchestrators must
// sweep locks older than a deadline themselves.
func (m *Manager) IsStale(maxAge time.Duration, workspace string) bool {
	held, ok := m.Store.Get(m.Backend.LockKey(workspace))
	if !ok {
		return false
	}
	var existing LockBody
	if err := json.Unmarshal([]byte(held), &existing); err != nil {
		return true
	}
	created, err := time.Parse("2006-01-02 15:04:05 -0700", existing.Created)
	if err != nil {
		return true
	}
	return time.Since(created) > maxAge
}

// newNonce returns a random-enough hex nonce without external packages.
func newNonce() string {
	buf := make([]byte, 16)
	seed := time.Now().UnixNano()
	for i := range buf {
		seed = seed*6364136223846793005 + 1442695040888963407
		buf[i] = byte(seed >> 33)
	}
	return fmt.Sprintf("%x-%x-%x-%x-%x", buf[0:4], buf[4:6], buf[6:8], buf[8:10], buf[10:16])
}

func main() {
	store := NewStore()
	backend := Backend{Bucket: "tf-state-prod", Key: "path/to/my/key", WorkspaceKeyPrefix: "env:"}
	fmt.Println("state object :", backend.StateKey("default"))
	fmt.Println("prod state   :", backend.StateKey("production"))
	fmt.Println("lock object  :", backend.LockKey("default"))
	fmt.Println("dynamodb key : LockID (String) -- conditional PutItem\n")

	alice := &Manager{Store: store, Backend: backend, Version: "1.16.0"}
	bob := &Manager{Store: store, Backend: backend, Version: "1.16.0"}

	lockID, _, _ := alice.Acquire("plan", "default")
	fmt.Println("[1] alice acquires  ->", lockID)

	_, held, _ := bob.Acquire("apply", "default")
	fmt.Println("[2] bob is refused:")
	fmt.Println("         Error: Error acquiring the state lock")
	fmt.Printf("         Lock Info: ID=%s Operation=%s Who=%s Created=%s\n",
		held.ID, held.Operation, held.Who, held.Created)

	fmt.Println("[3] alice releases  ->", alice.Release(lockID, "default"))
	fmt.Println("[3] bob releases    ->", bob.Release(lockID, "default"), "(not the owner)")

	prodID, _, _ := alice.Acquire("apply", "production")
	_, prodHeld := store.Get(backend.LockKey("production"))
	fmt.Println("[4] prod lock holds :", prodHeld)

	if _, err := bob.ForceUnlock(newNonce(), "production"); err != nil {
		fmt.Println("[5] wrong nonce     ->", err)
	}
	ok, _ := bob.ForceUnlock(prodID, "production")
	fmt.Println("[5] right nonce     ->", ok)

	staleID, _, _ := alice.Acquire("destroy", "default")
	var dead LockBody
	raw, _ := store.Get(backend.LockKey("default"))
	_ = json.Unmarshal([]byte(raw), &dead)
	dead.Created = time.Now().Add(-time.Hour).Format("2006-01-02 15:04:05 -0700")
	body, _ := json.Marshal(dead)
	store.Put(backend.LockKey("default"), string(body))
	fmt.Println("[6] stale detected  ->", alice.IsStale(time.Minute, "default"))
	ok, _ = alice.ForceUnlock(staleID, "default")
	fmt.Println("[6] sweep by nonce  ->", ok)
	_, still := store.Get(backend.LockKey("default"))
	fmt.Println("[6] lock removed    ->", !still)
}
