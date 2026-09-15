/* hmacsha256.h — SHA-256(FIPS 180-4) + HMAC-SHA-256(RFC 2104) 精简实现 */
#ifndef HMACSHA256_H
#define HMACSHA256_H

#include <stddef.h>
#include <stdint.h>

void sha256(const uint8_t *msg, size_t len, uint8_t out[32]);
void hmac_sha256(const uint8_t *key, size_t klen, const uint8_t *msg,
                 size_t mlen, uint8_t out[32]);

#endif
