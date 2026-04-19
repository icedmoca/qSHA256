# qSHA256 - Cryptographically Secure Cryptographic Library

[![CI](https://github.com/oracleagent/qSHA256/actions/workflows/ci.yml/badge.svg)](https://github.com/oracleagent/qSHA256/actions/workflows/ci.yml)
[![PyPI version](https://badge.fury.io/py/qsha256.svg)](https://badge.fury.io/py/qsha256)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**qSHA256 now provides cryptographically secure primitives built on pyca/cryptography and hashlib.**

A Python library providing secure cryptographic primitives including SHA-256 hashing, HMAC authentication, HKDF key derivation, AES-GCM authenticated encryption, and Ed25519 digital signatures. All implementations use vetted cryptographic libraries and include strict input validation for production use.

## Features

- **SHA-256 Hashing**: Secure hash function using Python's `hashlib`
- **HMAC Authentication**: Message authentication with constant-time comparison
- **HKDF Key Derivation**: Extract and expand key material securely
- **AES-GCM Encryption**: Authenticated encryption with associated data
- **Ed25519 Signatures**: Fast, secure digital signatures
- **Input Validation**: Strict type checking and size limits
- **Production Ready**: Built on cryptographically secure foundations

## Installation

```bash
python -m venv ~/venvs/qsha
source ~/venvs/qsha/bin/activate
git clone https://github.com/icedmoca/qSHA256.git
cd qSHA256
pip install .
```

## Quick Start

### SHA-256 Hashing

```python
import qsha256

# Hash some data
data = b"Hello, World!"
hash_result = qsha256.secure_sha256(data)
print(f"SHA-256: {hash_result.hex()}")
```

### HMAC Authentication

```python
import qsha256

# Generate a key
key = qsha256.generate_key(32)

# Create HMAC
message = b"Authenticate this message"
hmac_tag = qsha256.secure_hmac(key, message)

# Verify HMAC
is_valid = qsha256.secure_hmac_verify(key, message, hmac_tag)
print(f"HMAC valid: {is_valid}")
```

### AES-GCM Encryption

```python
import qsha256

# Generate encryption key
key = qsha256.generate_key(32)  # 256-bit key

# Encrypt data
plaintext = b"Sensitive information"
nonce, ciphertext = qsha256.aes_gcm_encrypt(key, plaintext)

# Decrypt data
decrypted = qsha256.aes_gcm_decrypt(key, nonce, ciphertext)
print(f"Decrypted: {decrypted}")
```

### Ed25519 Digital Signatures

```python
import qsha256

# Generate key pair
private_key, public_key = qsha256.ed25519_generate_keypair()

# Sign a message
message = b"Sign this document"
signature = qsha256.ed25519_sign(private_key, message)

# Verify signature
is_valid = qsha256.ed25519_verify(public_key, message, signature)
print(f"Signature valid: {is_valid}")
```

### HKDF Key Derivation

```python
import qsha256

# Derive keys from input key material
salt = b"random_salt"
info = b"context_info"
ikm = b"input_key_material"

derived_key = qsha256.hkdf_extract_expand(salt, info, ikm, 32)
print(f"Derived key: {derived_key.hex()}")
```

## API Reference

### Hash Functions

#### `secure_sha256(data: bytes) -> bytes`
Compute SHA-256 hash of input data.
- **data**: Input bytes to hash
- **Returns**: 32-byte SHA-256 hash

### Authentication

#### `secure_hmac(key: bytes, data: bytes) -> bytes`
Compute HMAC-SHA256 authentication tag.
- **key**: HMAC key (minimum 16 bytes)
- **data**: Data to authenticate
- **Returns**: 32-byte HMAC tag

#### `secure_hmac_verify(key: bytes, data: bytes, tag: bytes) -> bool`
Verify HMAC tag using constant-time comparison.
- **key**: HMAC key
- **data**: Original data
- **tag**: HMAC tag to verify
- **Returns**: True if valid, False otherwise

### Key Generation

#### `generate_key(length: int = 32) -> bytes`
Generate cryptographically secure random key.
- **length**: Key length in bytes (default: 32)
- **Returns**: Random key bytes

### Key Derivation

#### `hkdf_extract_expand(salt: bytes, info: bytes, ikm: bytes, length: int = 32) -> bytes`
Extract and expand key material using HKDF-SHA256.
- **salt**: Salt for key extraction
- **info**: Context information
- **ikm**: Input key material
- **length**: Output key length in bytes
- **Returns**: Derived key material

### Authenticated Encryption

#### `aes_gcm_encrypt(key: bytes, plaintext: bytes, aad: bytes = None) -> tuple[bytes, bytes]`
Encrypt data using AES-GCM authenticated encryption.
- **key**: AES key (16, 24, or 32 bytes)
- **plaintext**: Data to encrypt
- **aad**: Additional authenticated data (optional)
- **Returns**: (nonce, ciphertext_and_tag)

#### `aes_gcm_decrypt(key: bytes, nonce: bytes, ct: bytes, aad: bytes = None) -> bytes`
Decrypt data using AES-GCM authenticated encryption.
- **key**: AES key
- **nonce**: Nonce used for encryption
- **ct**: Ciphertext and tag
- **aad**: Additional authenticated data (optional)
- **Returns**: Decrypted plaintext

### Digital Signatures

#### `ed25519_generate_keypair() -> tuple[bytes, bytes]`
Generate Ed25519 key pair.
- **Returns**: (private_key, public_key) both 32 bytes

#### `ed25519_sign(private_key: bytes, message: bytes) -> bytes`
Sign a message using Ed25519.
- **private_key**: 32-byte private key
- **message**: Message to sign
- **Returns**: 64-byte signature

#### `ed25519_verify(public_key: bytes, message: bytes, signature: bytes) -> bool`
Verify Ed25519 signature.
- **public_key**: 32-byte public key
- **message**: Original message
- **signature**: 64-byte signature
- **Returns**: True if valid, False otherwise

## Security Features

- **Input Validation**: All functions validate input types and enforce size limits (default: 1 MB)
- **Constant-Time Operations**: HMAC verification uses constant-time comparison
- **Secure Random**: Key generation uses `secrets.token_bytes()`
- **Vetted Libraries**: Built on `hashlib`, `cryptography`, and `hmac` modules
- **Error Handling**: Clear error messages for invalid inputs

## Development

### Running Tests

```bash
# Install development dependencies
pip install -r requirements.txt

# Run tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=qsha256 --cov-report=html
```

### Code Quality

```bash
# Lint with ruff
ruff check qsha256/ tests/

# Security check with bandit
bandit -r qsha256/
```

## Experimental Demos

⚠️ **WARNING**: The `demos/` folder contains experimental quantum SHA-256 implementations for educational purposes only. These are **NOT SECURE** and should never be used in production.

To run experimental demos:
```bash
cd demos/
python demo.py
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests for new functionality
5. Ensure all tests pass
6. Submit a pull request

## Security

For security-related issues, please see [SECURITY.md](SECURITY.md) for our vulnerability disclosure policy.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for version history and changes.
