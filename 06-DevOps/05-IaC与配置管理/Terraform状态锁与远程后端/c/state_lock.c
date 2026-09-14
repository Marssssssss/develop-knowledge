/* Terraform state locking simulation (S3 lockfile + DynamoDB LockID semantics).
 * Refs read: HashiCorp "State: Locking" https://developer.hashicorp.com/terraform/language/state/locking
 *            HashiCorp "Backend Type: s3" https://developer.hashicorp.com/terraform/language/backend/s3
 * Mirrored: locking is automatic for state-writing ops and a failed acquire
 * ABORTS the run; force-unlock <ID> takes a nonce that must match the holder;
 * S3 paths are <key> (default) and env:/<ws>/<key> with the lock at
 * <key>.tflock when use_lockfile=true; DynamoDB (deprecated) used a String
 * partition key named LockID plus conditional PutItem -- the same create-only
 * primitive as S3's `If-None-Match: *`.
 * Build: gcc -O2 -Wall -Wextra -pedantic state_lock.c -o state_lock
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define MAX_OBJECTS 16
#define MAX_KEY 160
#define MAX_BODY 256

/* ---- object store: the only primitive we need is a create-only write ---- */

typedef struct {
    char key[MAX_KEY];
    char body[MAX_BODY];
} Object;

typedef struct {
    Object items[MAX_OBJECTS];
    int count;
} Store;

static Object *store_find(Store *s, const char *key)
{
    int i;
    for (i = 0; i < s->count; i++) {
        if (strcmp(s->items[i].key, key) == 0) {
            return &s->items[i];
        }
    }
    return NULL;
}

static const char *store_get(Store *s, const char *key)
{
    Object *o = store_find(s, key);
    return o ? o->body : NULL;
}

/* Conditional write: succeeds only when the key is absent. S3's
 * `If-None-Match: *` and DynamoDB's attribute_not_exists(LockID) both reduce
 * to this, which is the whole of Terraform's lock acquisition. */
static int store_put_if_absent(Store *s, const char *key, const char *body)
{
    Object *o;
    if (store_find(s, key) != NULL || s->count >= MAX_OBJECTS) {
        return 0;
    }
    o = &s->items[s->count++];
    snprintf(o->key, MAX_KEY, "%s", key);
    snprintf(o->body, MAX_BODY, "%s", body);
    return 1;
}

static void store_put(Store *s, const char *key, const char *body)
{
    Object *o = store_find(s, key);
    if (o == NULL) {
        if (s->count >= MAX_OBJECTS) {
            return;
        }
        o = &s->items[s->count++];
        snprintf(o->key, MAX_KEY, "%s", key);
    }
    snprintf(o->body, MAX_BODY, "%s", body);
}

static int store_delete(Store *s, const char *key)
{
    int i;
    for (i = 0; i < s->count; i++) {
        if (strcmp(s->items[i].key, key) == 0) {
            s->items[i] = s->items[s->count - 1];
            s->count--;
            return 1;
        }
    }
    return 0;
}

/* ---- backend path layout (S3) ---- */

typedef struct {
    const char *bucket;
    const char *key;
    const char *workspace_key_prefix; /* documented default: "env:" */
} Backend;

static void state_key(const Backend *b, const char *ws, char *out, size_t n)
{
    if (strcmp(ws, "default") == 0) {
        snprintf(out, n, "%s", b->key);
    } else {
        snprintf(out, n, "%s/%s/%s", b->workspace_key_prefix, ws, b->key);
    }
}

static void lock_key(const Backend *b, const char *ws, char *out, size_t n)
{
    char sk[MAX_KEY];
    state_key(b, ws, sk, sizeof(sk));
    snprintf(out, n, "%s.tflock", sk);
}

/* ---- lock payload: a flat key=value record standing in for the backends' JSON */

typedef struct {
    char id[40];
    char operation[24];
    char who[48];
    char version[16];
    long created_epoch;
} LockBody;

static void encode_lock(const LockBody *lb, char *out, size_t n)
{
    snprintf(out, n, "ID=%s;Operation=%s;Who=%s;Version=%s;Created=%ld",
             lb->id, lb->operation, lb->who, lb->version, lb->created_epoch);
}

static void set_field(char *dst, size_t n, const char *val, size_t vlen)
{
    snprintf(dst, n, "%.*s", (int)vlen, val);
}

/* Returns 0 on a malformed payload: an unreadable lock must fail closed. */
static int parse_lock(const char *raw, LockBody *lb)
{
    const char *p = raw;
    memset(lb, 0, sizeof(*lb));
    if (raw == NULL) {
        return 0;
    }
    while (*p != '\0') {
        const char *eq = strchr(p, '=');
        const char *end;
        size_t klen, vlen;
        if (eq == NULL) {
            return 0;
        }
        klen = (size_t)(eq - p);
        end = strchr(eq + 1, ';');
        vlen = end ? (size_t)(end - (eq + 1)) : strlen(eq + 1);
        if (klen == 2 && strncmp(p, "ID", 2) == 0) {
            set_field(lb->id, sizeof(lb->id), eq + 1, vlen);
        } else if (klen == 9 && strncmp(p, "Operation", 9) == 0) {
            set_field(lb->operation, sizeof(lb->operation), eq + 1, vlen);
        } else if (klen == 3 && strncmp(p, "Who", 3) == 0) {
            set_field(lb->who, sizeof(lb->who), eq + 1, vlen);
        } else if (klen == 7 && strncmp(p, "Version", 7) == 0) {
            set_field(lb->version, sizeof(lb->version), eq + 1, vlen);
        } else if (klen == 7 && strncmp(p, "Created", 7) == 0) {
            lb->created_epoch = strtol(eq + 1, NULL, 10);
        }
        p = end ? end + 1 : eq + 1 + vlen;
    }
    return 1;
}

typedef enum { ACQ_OK = 0, ACQ_HELD = 1, ACQ_BAD_PAYLOAD = 2 } AcquireResult;

static AcquireResult acquire(Store *s, const Backend *b, const char *op,
                             const char *ws, const char *nonce, LockBody *held)
{
    char lk[MAX_KEY];
    char body[MAX_BODY];
    LockBody lb;
    lock_key(b, ws, lk, sizeof(lk));
    snprintf(lb.id, sizeof(lb.id), "%s", nonce);
    snprintf(lb.operation, sizeof(lb.operation), "%s", op);
    snprintf(lb.who, sizeof(lb.who), "runner@%ld", (long)getpid());
    snprintf(lb.version, sizeof(lb.version), "1.16.0");
    lb.created_epoch = (long)time(NULL);
    encode_lock(&lb, body, sizeof(body));
    if (store_put_if_absent(s, lk, body)) {
        return ACQ_OK;
    }
    return parse_lock(store_get(s, lk), held) ? ACQ_HELD : ACQ_BAD_PAYLOAD;
}

/* compare-and-delete: only the recorded owner may release. */
static int release(Store *s, const Backend *b, const char *nonce, const char *ws)
{
    char lk[MAX_KEY];
    LockBody lb;
    lock_key(b, ws, lk, sizeof(lk));
    if (!parse_lock(store_get(s, lk), &lb) || strcmp(lb.id, nonce) != 0) {
        return 0;
    }
    return store_delete(s, lk);
}

/* `terraform force-unlock <ID>`: the ID is a nonce, a mismatch must be refused. */
static int force_unlock(Store *s, const Backend *b, const char *nonce,
                        const char *ws, char *err, size_t errn)
{
    char lk[MAX_KEY];
    LockBody lb;
    lock_key(b, ws, lk, sizeof(lk));
    if (!parse_lock(store_get(s, lk), &lb)) {
        snprintf(err, errn, "no readable lock to unlock");
        return 0;
    }
    if (strcmp(lb.id, nonce) != 0) {
        snprintf(err, errn, "invalid lock id '%s': does not match the holder", nonce);
        return 0;
    }
    store_delete(s, lk);
    return 1;
}

/* Terraform has no lock TTL: a crashed holder leaves the lock forever, so
 * automation must sweep by age. */
static int is_stale(Store *s, const Backend *b, long max_age, const char *ws)
{
    char lk[MAX_KEY];
    LockBody lb;
    lock_key(b, ws, lk, sizeof(lk));
    if (!parse_lock(store_get(s, lk), &lb)) {
        return 0;
    }
    return (long)time(NULL) - lb.created_epoch > max_age;
}

int main(void)
{
    Store store;
    Backend backend;
    LockBody held;
    LockBody patched;
    char buf[MAX_KEY];
    char raw[MAX_BODY];
    char err[128];
    int rc;

    backend.bucket = "tf-state-prod";
    backend.key = "path/to/my/key";
    backend.workspace_key_prefix = "env:";
    store.count = 0;

    state_key(&backend, "default", buf, sizeof(buf));
    printf("state object : %s\n", buf);
    state_key(&backend, "production", buf, sizeof(buf));
    printf("prod state   : %s\n", buf);
    lock_key(&backend, "default", buf, sizeof(buf));
    printf("lock object  : %s\n", buf);
    printf("dynamodb key : LockID (String) -- conditional PutItem\n\n");

    rc = acquire(&store, &backend, "plan", "default", "alice-0001", &held);
    printf("[1] alice acquires  -> rc=%d nonce=%s\n", rc, "alice-0001");

    rc = acquire(&store, &backend, "apply", "default", "bob-0002", &held);
    if (rc == ACQ_HELD) {
        printf("[2] bob is refused:\n");
        printf("         Error: Error acquiring the state lock\n");
        printf("         Lock Info: ID=%s Operation=%s Who=%s Version=%s\n",
               held.id, held.operation, held.who, held.version);
        printf("    (terraform stops here unless --lock=false is passed)\n");
    }

    printf("[3] alice releases  -> %d\n",
           release(&store, &backend, "alice-0001", "default"));
    printf("[3] bob releases    -> %d (not the owner)\n",
           release(&store, &backend, "alice-0001", "default"));

    rc = acquire(&store, &backend, "apply", "production", "prod-0003", &held);
    lock_key(&backend, "production", buf, sizeof(buf));
    printf("[4] prod lock holds : rc=%d present=%d\n", rc,
           store_get(&store, buf) != NULL);

    if (!force_unlock(&store, &backend, "wrong-nonce", "production", err, sizeof(err))) {
        printf("[5] wrong nonce     -> %s\n", err);
    }
    printf("[5] right nonce     -> %d\n",
           force_unlock(&store, &backend, "prod-0003", "production", err, sizeof(err)));

    acquire(&store, &backend, "destroy", "default", "crash-0007", &held);
    lock_key(&backend, "default", buf, sizeof(buf));
    parse_lock(store_get(&store, buf), &patched);
    patched.created_epoch = (long)time(NULL) - 3600;
    encode_lock(&patched, raw, sizeof(raw));
    store_put(&store, buf, raw);
    printf("[6] stale detected  -> %d\n", is_stale(&store, &backend, 60, "default"));
    printf("[6] sweep by nonce  -> %d\n",
           force_unlock(&store, &backend, "crash-0007", "default", err, sizeof(err)));
    printf("[6] lock removed    -> %d\n", store_get(&store, buf) == NULL);

    return 0;
}
