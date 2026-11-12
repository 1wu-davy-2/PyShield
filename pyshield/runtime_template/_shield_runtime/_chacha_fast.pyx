# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, initializedcheck=False

"""Native ChaCha20 keystream used by PyShield's dependency-free cipher.

This module is compiled into the runtime by ``pyshield build --runtime-native``.
The pure Python implementation in ``_crypto.py`` remains the fallback.
"""

from libc.stdint cimport uint8_t, uint32_t


cdef inline uint32_t _rotl(uint32_t value, int count) noexcept nogil:
    return (value << count) | (value >> (32 - count))


cdef void _block(uint32_t* out, const uint32_t* key, uint32_t counter, const uint32_t* nonce) noexcept nogil:
    cdef uint32_t x0 = 0x61707865
    cdef uint32_t x1 = 0x3320646E
    cdef uint32_t x2 = 0x79622D32
    cdef uint32_t x3 = 0x6B206574
    cdef uint32_t x4 = key[0]
    cdef uint32_t x5 = key[1]
    cdef uint32_t x6 = key[2]
    cdef uint32_t x7 = key[3]
    cdef uint32_t x8 = key[4]
    cdef uint32_t x9 = key[5]
    cdef uint32_t x10 = key[6]
    cdef uint32_t x11 = key[7]
    cdef uint32_t x12 = counter
    cdef uint32_t x13 = nonce[0]
    cdef uint32_t x14 = nonce[1]
    cdef uint32_t x15 = nonce[2]

    cdef uint32_t s0 = x0
    cdef uint32_t s1 = x1
    cdef uint32_t s2 = x2
    cdef uint32_t s3 = x3
    cdef uint32_t s4 = x4
    cdef uint32_t s5 = x5
    cdef uint32_t s6 = x6
    cdef uint32_t s7 = x7
    cdef uint32_t s8 = x8
    cdef uint32_t s9 = x9
    cdef uint32_t s10 = x10
    cdef uint32_t s11 = x11
    cdef uint32_t s12 = x12
    cdef uint32_t s13 = x13
    cdef uint32_t s14 = x14
    cdef uint32_t s15 = x15

    cdef int round
    for round in range(10):
        x0 += x4; x12 ^= x0; x12 = _rotl(x12, 16)
        x8 += x12; x4 ^= x8; x4 = _rotl(x4, 12)
        x0 += x4; x12 ^= x0; x12 = _rotl(x12, 8)
        x8 += x12; x4 ^= x8; x4 = _rotl(x4, 7)

        x1 += x5; x13 ^= x1; x13 = _rotl(x13, 16)
        x9 += x13; x5 ^= x9; x5 = _rotl(x5, 12)
        x1 += x5; x13 ^= x1; x13 = _rotl(x13, 8)
        x9 += x13; x5 ^= x9; x5 = _rotl(x5, 7)

        x2 += x6; x14 ^= x2; x14 = _rotl(x14, 16)
        x10 += x14; x6 ^= x10; x6 = _rotl(x6, 12)
        x2 += x6; x14 ^= x2; x14 = _rotl(x14, 8)
        x10 += x14; x6 ^= x10; x6 = _rotl(x6, 7)

        x3 += x7; x15 ^= x3; x15 = _rotl(x15, 16)
        x11 += x15; x7 ^= x11; x7 = _rotl(x7, 12)
        x3 += x7; x15 ^= x3; x15 = _rotl(x15, 8)
        x11 += x15; x7 ^= x11; x7 = _rotl(x7, 7)

        x0 += x5; x15 ^= x0; x15 = _rotl(x15, 16)
        x10 += x15; x5 ^= x10; x5 = _rotl(x5, 12)
        x0 += x5; x15 ^= x0; x15 = _rotl(x15, 8)
        x10 += x15; x5 ^= x10; x5 = _rotl(x5, 7)

        x1 += x6; x12 ^= x1; x12 = _rotl(x12, 16)
        x11 += x12; x6 ^= x11; x6 = _rotl(x6, 12)
        x1 += x6; x12 ^= x1; x12 = _rotl(x12, 8)
        x11 += x12; x6 ^= x11; x6 = _rotl(x6, 7)

        x2 += x7; x13 ^= x2; x13 = _rotl(x13, 16)
        x8 += x13; x7 ^= x8; x7 = _rotl(x7, 12)
        x2 += x7; x13 ^= x2; x13 = _rotl(x13, 8)
        x8 += x13; x7 ^= x8; x7 = _rotl(x7, 7)

        x3 += x4; x14 ^= x3; x14 = _rotl(x14, 16)
        x9 += x14; x4 ^= x9; x4 = _rotl(x4, 12)
        x3 += x4; x14 ^= x3; x14 = _rotl(x14, 8)
        x9 += x14; x4 ^= x9; x4 = _rotl(x4, 7)

    out[0] = x0 + s0
    out[1] = x1 + s1
    out[2] = x2 + s2
    out[3] = x3 + s3
    out[4] = x4 + s4
    out[5] = x5 + s5
    out[6] = x6 + s6
    out[7] = x7 + s7
    out[8] = x8 + s8
    out[9] = x9 + s9
    out[10] = x10 + s10
    out[11] = x11 + s11
    out[12] = x12 + s12
    out[13] = x13 + s13
    out[14] = x14 + s14
    out[15] = x15 + s15


def chacha20_xor(bytes key, bytes nonce, bytes data, unsigned int counter=1):
    if len(key) != 32:
        raise ValueError("ChaCha20 key must be 32 bytes")
    if len(nonce) != 12:
        raise ValueError("ChaCha20 nonce must be 12 bytes")

    cdef Py_ssize_t size = len(data)
    if size == 0:
        return b""

    cdef const uint8_t[:] key_view = key
    cdef const uint8_t[:] nonce_view = nonce
    cdef const uint8_t[:] source = data
    cdef uint8_t[:] result = bytearray(size)
    cdef uint32_t key_words[8]
    cdef uint32_t nonce_words[3]
    cdef uint32_t block_words[16]
    cdef uint8_t block_bytes[64]
    cdef Py_ssize_t index
    cdef Py_ssize_t offset = 0
    cdef Py_ssize_t chunk
    cdef uint32_t word
    cdef uint32_t block_counter = counter

    for index in range(8):
        key_words[index] = (
            <uint32_t>key_view[index * 4]
            | (<uint32_t>key_view[index * 4 + 1] << 8)
            | (<uint32_t>key_view[index * 4 + 2] << 16)
            | (<uint32_t>key_view[index * 4 + 3] << 24)
        )
    for index in range(3):
        nonce_words[index] = (
            <uint32_t>nonce_view[index * 4]
            | (<uint32_t>nonce_view[index * 4 + 1] << 8)
            | (<uint32_t>nonce_view[index * 4 + 2] << 16)
            | (<uint32_t>nonce_view[index * 4 + 3] << 24)
        )

    while offset < size:
        _block(block_words, key_words, block_counter, nonce_words)
        block_counter += 1
        for index in range(16):
            word = block_words[index]
            block_bytes[index * 4] = <uint8_t>(word & 0xFF)
            block_bytes[index * 4 + 1] = <uint8_t>((word >> 8) & 0xFF)
            block_bytes[index * 4 + 2] = <uint8_t>((word >> 16) & 0xFF)
            block_bytes[index * 4 + 3] = <uint8_t>((word >> 24) & 0xFF)
        chunk = size - offset
        if chunk > 64:
            chunk = 64
        for index in range(chunk):
            result[offset + index] = source[offset + index] ^ block_bytes[index]
        offset += chunk

    return bytes(result)
