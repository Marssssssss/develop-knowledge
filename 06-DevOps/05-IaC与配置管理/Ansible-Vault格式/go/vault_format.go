// Ansible Vault payload format: armor, key derivation, HMAC, AES-256-CTR.
// Ref read: Ansible "Format of files encrypted with Ansible Vault" and
// "Ansible Vault payload format 1.1 - 1.2"
// https://docs.ansible.com/projects/ansible-core/devel/vault_guide/vault_using_encrypted_content.html
// plus RFC 2104 (HMAC), RFC 5652 §6.3 (padding), NIST SP 800-38A §6.5 (CTR).
// Layout: header $ANSIBLE_VAULT;1.1;AES256; vaulttext = hexlify(salt)\n
// hexlify(hmac)\n hexlify(ciphertext) wrapped at 80 cols; keys = PBKDF2(SHA256,
// salt, 10000 iters) -> 80B ([0:32] cipher, [32:64] HMAC, [64:80] IV); HMAC is
// RFC 2104 style over the ciphertext; AES-CTR seeded from the 128-bit IV.
// Go's stdlib has crypto/aes, so unlike the Python twin (which hand-rolls AES
// to stay dependency-free) this uses the vetted standard library; only PBKDF2
// and PKCS#7 are hand-rolled.
package main

import (
	"bytes"
	"crypto/aes"
	"crypto/cipher"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
	"strings"
)

const (
	headerPrefix = "$ANSIBLE_VAULT"
	lineWidth    = 80
	pbkdf2Iter   = 10000
	derivedLen   = 80 // 32 cipher key + 32 HMAC key + 16 IV
	saltLen      = 32
)

var (
	errBadHeader = errors.New("not an Ansible Vault payload (bad header)")
	errBadHex    = errors.New("vaulttext is not valid hex")
	errAuth      = errors.New("HMAC mismatch: wrong password or tampered ciphertext")
)

// Vault encrypts/decrypts the documented payload layout for one password.
type Vault struct{ Password []byte }

// derive splits the 80-byte PBKDF2 output exactly as documented.
func (v *Vault) derive(salt []byte) (cipherKey, hmacKey, iv []byte) {
	dk := pbkdf2SHA256(v.Password, salt, pbkdf2Iter, derivedLen)
	return dk[:32], dk[32:64], dk[64:80]
}

// pbkdf2SHA256 is RFC 8018 §5.2; the standard library ships no PBKDF2 helper.
func pbkdf2SHA256(password, salt []byte, iter, keyLen int) []byte {
	prf := hmac.New(sha256.New, password)
	blocks := (keyLen + prf.Size() - 1) / prf.Size()
	out := make([]byte, 0, blocks*prf.Size())
	var idx [4]byte
	for block := 1; block <= blocks; block++ {
		prf.Reset()
		prf.Write(salt)
		binary.BigEndian.PutUint32(idx[:], uint32(block))
		prf.Write(idx[:])
		u := prf.Sum(nil)
		t := append([]byte{}, u...)
		for i := 1; i < iter; i++ {
			prf.Reset()
			prf.Write(u)
			u = prf.Sum(nil)
			for j := range t {
				t[j] ^= u[j]
			}
		}
		out = append(out, t...)
	}
	return out[:keyLen]
}

func armor(payload string) string {
	text := hex.EncodeToString([]byte(payload))
	var b strings.Builder
	for i := 0; i < len(text); i += lineWidth {
		end := i + lineWidth
		if end > len(text) {
			end = len(text)
		}
		b.WriteString(text[i:end])
		b.WriteByte('\n')
	}
	return b.String()
}

// unarmor undoes the OUTER hexlify only: the result is the ASCII "salt\nhmac\nct".
func unarmor(lines []string) (string, error) {
	var joined strings.Builder
	for _, ln := range lines {
		joined.WriteString(strings.TrimSpace(ln))
	}
	raw, err := hex.DecodeString(joined.String())
	if err != nil {
		return "", errBadHex
	}
	return string(raw), nil
}

// Encrypt returns the complete vault file text for a plaintext secret.
func (v *Vault) Encrypt(plaintext, vaultID string) (string, error) {
	salt := make([]byte, saltLen)
	if _, err := rand.Read(salt); err != nil {
		return "", err
	}
	cipherKey, hmacKey, iv := v.derive(salt)
	block, err := aes.NewCipher(cipherKey)
	if err != nil {
		return "", err
	}
	padded := pkcs7Pad([]byte(plaintext))
	ciphertext := make([]byte, len(padded))
	// The 128-bit counter block is seeded from the integer IV (SP 800-38A).
	cipher.NewCTR(block, iv).XORKeyStream(ciphertext, padded)

	mac := hmac.New(sha256.New, hmacKey)
	mac.Write(ciphertext)
	body := hex.EncodeToString(salt) + "\n" +
		hex.EncodeToString(mac.Sum(nil)) + "\n" +
		hex.EncodeToString(ciphertext)

	head := headerPrefix + ";1.1;AES256"
	if vaultID != "" {
		head = headerPrefix + ";1.2;AES256;" + vaultID
	}
	return head + "\n" + armor(body), nil
}

// Decrypt authenticates FIRST, then decrypts: CTR is malleable, so the HMAC is
// the only thing distinguishing a flipped bit from a legitimate edit.
func (v *Vault) Decrypt(text string) (string, error) {
	lines := strings.Split(strings.ReplaceAll(text, "\r\n", "\n"), "\n")
	if len(lines) == 0 || !strings.HasPrefix(lines[0], headerPrefix+";") {
		return "", errBadHeader
	}
	fields := strings.Split(lines[0], ";")
	if len(fields) < 3 {
		return "", errBadHeader
	}
	if fields[2] != "AES256" {
		return "", fmt.Errorf("unsupported cipher %q", fields[2])
	}
	if fields[1] != "1.1" && fields[1] != "1.2" {
		return "", fmt.Errorf("unsupported vault format version %q", fields[1])
	}
	body, err := unarmor(lines[1:])
	if err != nil {
		return "", err
	}
	parts := strings.Split(body, "\n")
	if len(parts) != 3 {
		return "", fmt.Errorf("expected salt/hmac/ciphertext, got %d parts", len(parts))
	}
	salt, err1 := hex.DecodeString(parts[0])
	digest, err2 := hex.DecodeString(parts[1])
	ciphertext, err3 := hex.DecodeString(parts[2])
	if err1 != nil || err2 != nil || err3 != nil {
		return "", errBadHex
	}

	cipherKey, hmacKey, iv := v.derive(salt)
	mac := hmac.New(sha256.New, hmacKey)
	mac.Write(ciphertext)
	if !hmac.Equal(mac.Sum(nil), digest) {
		return "", errAuth
	}
	block, err := aes.NewCipher(cipherKey)
	if err != nil {
		return "", err
	}
	plain := make([]byte, len(ciphertext))
	cipher.NewCTR(block, iv).XORKeyStream(plain, ciphertext)
	return string(pkcs7Unpad(plain))
}

// pkcs7Pad always adds 1..16 bytes, so a 16-byte input grows by a full block
// (RFC 5652 §6.3) -- that is what makes unpadding unambiguous.
func pkcs7Pad(data []byte) []byte {
	pad := aes.BlockSize - len(data)%aes.BlockSize
	return append(append([]byte{}, data...), bytes.Repeat([]byte{byte(pad)}, pad)...)
}

func pkcs7Unpad(data []byte) ([]byte, error) {
	if len(data) == 0 || len(data)%aes.BlockSize != 0 {
		return nil, errors.New("ciphertext length is not a multiple of the block size")
	}
	pad := int(data[len(data)-1])
	want := bytes.Repeat([]byte{byte(pad)}, pad)
	if pad < 1 || pad > aes.BlockSize || !bytes.Equal(data[len(data)-pad:], want) {
		return nil, errors.New("invalid padding")
	}
	return data[:len(data)-pad], nil
}

// tamper flips the final hex nibble of the armor, i.e. one ciphertext bit.
func tamper(text string) string {
	lines := strings.Split(strings.TrimRight(text, "\n"), "\n")
	i, last := len(lines)-1, []byte(lines[len(lines)-1])
	if last[len(last)-1] == '0' {
		last[len(last)-1] = '1'
	} else {
		last[len(last)-1] = '0'
	}
	lines[i] = string(last)
	return strings.Join(lines, "\n") + "\n"
}

// selftest pins the primitives to published expectations.
func selftest() {
	key, _ := hex.DecodeString("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f")
	plain, _ := hex.DecodeString("00112233445566778899aabbccddeeff")
	block, _ := aes.NewCipher(key)
	got := make([]byte, 16)
	block.Encrypt(got, plain)
	if hex.EncodeToString(got) != "8ea2b7ca516745bfeafc49904b496089" {
		panic("crypto/aes disagrees with FIPS-197 C.3: " + hex.EncodeToString(got))
	}
	one := pbkdf2SHA256([]byte("password"), []byte("salt"), 1, 32)
	many := pbkdf2SHA256([]byte("password"), []byte("salt"), 10000, 32)
	if bytes.Equal(one, many) {
		panic("PBKDF2 ignoring the iteration count")
	}
	fmt.Println("  [selftest] crypto/aes matches FIPS-197 C.3; PBKDF2 honours iterations")
}
func main() {
	selftest()
	secret := "the_secret: hunter2"
	v := &Vault{Password: []byte("a_password_file")}

	fmt.Println("\n== key derivation (PBKDF2-HMAC-SHA256, 10000 iterations, 80 bytes) ==")
	salt := make([]byte, saltLen)
	_, _ = rand.Read(salt)
	ck, hk, iv := v.derive(salt)
	fmt.Printf("  salt %dB | cipher key %dB | HMAC key %dB | IV %dB\n",
		len(salt), len(ck), len(hk), len(iv))

	fmt.Println("\n== encrypt (format 1.1, no vault id) ==")
	blob, err := v.Encrypt(secret, "")
	if err != nil {
		panic(err)
	}
	armorLines := strings.Split(strings.TrimRight(blob, "\n"), "\n")
	for _, ln := range armorLines[:4] {
		fmt.Println("  " + ln)
	}
	fmt.Printf("  ... (%d armor lines total)\n", len(armorLines)-1)

	fmt.Println("\n== decrypt ==")
	back, err := v.Decrypt(blob)
	if err != nil {
		panic(err)
	}
	fmt.Printf("  recovered: %q\n", back)
	if back != secret {
		panic("round-trip lost data")
	}

	fmt.Println("\n== tampered ciphertext (one hex nibble flipped) ==")
	if _, err := v.Decrypt(tamper(blob)); err != nil {
		fmt.Println("  rejected:", err)
	} else {
		panic("tampering was not detected")
	}

	fmt.Println("\n== wrong password ==")
	if _, err := (&Vault{Password: []byte("not_the_password")}).Decrypt(blob); err != nil {
		fmt.Println("  rejected:", err)
	}

	fmt.Println("\n== format 1.2 with a vault-id label ==")
	dev, err := v.Encrypt("the_dev_secret: foooodev", "dev")
	if err != nil {
		panic(err)
	}
	fmt.Println("  header:", strings.Split(dev, "\n")[0])
	back, err = v.Decrypt(dev)
	if err != nil {
		panic(err)
	}
	fmt.Printf("  recovered: %q\n", back)

	fmt.Println("\n== randomness: identical input -> distinct payloads ==")
	seen := map[string]bool{}
	for i := 0; i < 4; i++ {
		b, _ := v.Encrypt(secret, "")
		seen[b] = true
	}
	fmt.Printf("  %d distinct payloads from 4 encryptions of the same string\n", len(seen))

	fmt.Println("\n== padding (RFC 5652) on an exact multiple of 16 ==")
	for _, n := range []int{15, 16, 17} {
		p := pkcs7Pad(bytes.Repeat([]byte("x"), n))
		fmt.Printf("  %2dB -> %2dB (pad=%d)\n", n, len(p), p[len(p)-1])
	}
}
