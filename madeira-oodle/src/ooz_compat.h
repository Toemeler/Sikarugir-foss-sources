/* madeira-oodle: what ooz's kraken.cpp took from stdafx.h, <intrin.h> and
 * <Windows.h>, for ARM64 and ARM64EC (and any other little-endian host the
 * host check builds it on).
 *
 * Every SSE2 intrinsic kraken.cpp uses is given here with the x86 semantics,
 * lane for lane, on a plain 16-byte vector; the compiler turns the loops into
 * NEON. Nothing else in kraken.cpp is changed apart from its include line, so
 * the decoder stays ooz's. tests/host/check-oodle-native.py decodes streams made
 * by the game's own oo2core_6_win64.dll with this exact header. */
#pragma once

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stddef.h>
#include <assert.h>

typedef unsigned char byte;
typedef unsigned char uint8;
typedef unsigned int uint32;
typedef unsigned long long uint64;
typedef signed long long int64;
typedef signed int int32;
typedef unsigned short uint16;
typedef signed short int16;
typedef unsigned int uint;

#ifndef __forceinline
#define __forceinline inline __attribute__((always_inline))
#endif

/* _BitScanReverse/_BitScanForward: kraken.cpp declares the index as unsigned
 * long; macros keep that working whatever unsigned long's width is. */
#define _BitScanReverse(pidx, x) \
    ((uint32)(x) ? (*(pidx) = 31 - __builtin_clz((uint32)(x)), (unsigned char)1) : (unsigned char)0)
#define _BitScanForward(pidx, x) \
    ((uint32)(x) ? (*(pidx) = __builtin_ctz((uint32)(x)), (unsigned char)1) : (unsigned char)0)

/* MinGW's stdlib already declares these names. Macros avoid redeclaring CRT
 * functions with static linkage and preserve the original decoder's spelling. */
#define _byteswap_ulong(v) __builtin_bswap32((uint32)(v))
#define _byteswap_uint64(v) __builtin_bswap64((uint64)(v))
#define _byteswap_ushort(v) __builtin_bswap16((uint16)(v))
static inline uint32 madeira_rotl(uint32 v, int n) {
    n &= 31;
    return n ? (v << n) | (v >> (32 - n)) : v;
}
#define _rotl(v, n) madeira_rotl((uint32)(v), (n))

/* SSE2 subset. __m128i/__m128 are the same 16 bytes; __m64 only names the
 * destination of _mm_storeh_pi. */
#ifdef MADEIRA_OOZ_X86_REFERENCE_SSE
#include <emmintrin.h>
#else
typedef struct { uint8 b[16]; } __m128i;
typedef __m128i __m128;
typedef struct { uint8 b[8]; } __m64;
#define _MM_HINT_T0 3

static inline __m128i _mm_loadu_si128(const __m128i *p) { __m128i r; memcpy(&r, p, 16); return r; }
static inline void _mm_storeu_si128(__m128i *p, __m128i v) { memcpy(p, &v, 16); }
static inline __m128i _mm_loadl_epi64(const __m128i *p) { __m128i r; memcpy(&r, p, 8); memset(r.b + 8, 0, 8); return r; }
static inline void _mm_storel_epi64(__m128i *p, __m128i v) { memcpy(p, v.b, 8); }
static inline __m128 _mm_castsi128_ps(__m128i v) { return v; }
static inline void _mm_storeh_pi(__m64 *p, __m128 v) { memcpy(p, v.b + 8, 8); }

static inline __m128i _mm_set1_epi8(char c) { __m128i r; memset(r.b, (uint8)c, 16); return r; }
static inline __m128i _mm_set1_epi32(int x) {
    __m128i r; int i;
    for (i = 0; i < 4; i++) memcpy(r.b + 4 * i, &x, 4);
    return r;
}
static inline __m128i _mm_add_epi8(__m128i a, __m128i b) { __m128i r; int i; for (i = 0; i < 16; i++) r.b[i] = (uint8)(a.b[i] + b.b[i]); return r; }
static inline __m128i _mm_sub_epi8(__m128i a, __m128i b) { __m128i r; int i; for (i = 0; i < 16; i++) r.b[i] = (uint8)(a.b[i] - b.b[i]); return r; }
static inline __m128i _mm_and_si128(__m128i a, __m128i b) { __m128i r; int i; for (i = 0; i < 16; i++) r.b[i] = a.b[i] & b.b[i]; return r; }
static inline __m128i _mm_xor_si128(__m128i a, __m128i b) { __m128i r; int i; for (i = 0; i < 16; i++) r.b[i] = a.b[i] ^ b.b[i]; return r; }
static inline __m128i _mm_max_epu8(__m128i a, __m128i b) { __m128i r; int i; for (i = 0; i < 16; i++) r.b[i] = a.b[i] > b.b[i] ? a.b[i] : b.b[i]; return r; }
static inline __m128i _mm_cmpeq_epi8(__m128i a, __m128i b) { __m128i r; int i; for (i = 0; i < 16; i++) r.b[i] = a.b[i] == b.b[i] ? 0xff : 0; return r; }
static inline int _mm_movemask_epi8(__m128i a) { int m = 0, i; for (i = 0; i < 16; i++) m |= (a.b[i] >> 7) << i; return m; }

/* Interleave the low (high) eight bytes of a and b: a0 b0 a1 b1 ... */
static inline __m128i _mm_unpacklo_epi8(__m128i a, __m128i b) { __m128i r; int i; for (i = 0; i < 8; i++) { r.b[2 * i] = a.b[i]; r.b[2 * i + 1] = b.b[i]; } return r; }
static inline __m128i _mm_unpackhi_epi8(__m128i a, __m128i b) { __m128i r; int i; for (i = 0; i < 8; i++) { r.b[2 * i] = a.b[8 + i]; r.b[2 * i + 1] = b.b[8 + i]; } return r; }

/* Whole-register byte shift toward higher addresses (x86 little-endian lanes). */
static inline __m128i _mm_slli_si128(__m128i a, int n) {
    __m128i r; int i;
    for (i = 0; i < 16; i++) r.b[i] = (n > 15 || i < n) ? 0 : a.b[i - n];
    return r;
}
/* Logical right shift of each 16-bit lane. */
static inline __m128i _mm_srli_epi16(__m128i a, int n) {
    __m128i r; int i;
    for (i = 0; i < 8; i++) {
        uint16 v = (uint16)(a.b[2 * i] | (a.b[2 * i + 1] << 8));
        v = n > 15 ? 0 : (uint16)(v >> n);
        r.b[2 * i] = (uint8)v; r.b[2 * i + 1] = (uint8)(v >> 8);
    }
    return r;
}
/* Dword shuffle by an immediate (two bits per destination dword). */
static inline __m128i _mm_shuffle_epi32(__m128i a, int imm) {
    __m128i r; int i;
    for (i = 0; i < 4; i++) memcpy(r.b + 4 * i, a.b + 4 * ((imm >> (2 * i)) & 3), 4);
    return r;
}
#define _mm_prefetch(p, hint) __builtin_prefetch((const void *)(p))
#endif
