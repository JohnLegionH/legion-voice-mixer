/*! \file    joincap.c
 * \author   Legion Voice Mixer project
 * \copyright GNU General Public License v3
 * \brief    Implementation of the sim-issued join capability (see joincap.h, design §11).
 */

#include "joincap.h"

#include <openssl/hmac.h>
#include <openssl/crypto.h>

#include <stdlib.h>
#include <string.h>

/* The decoded payload never approaches this: 8 fields of at most 40 bytes plus separators. */
#define SLV_JOINCAP_PAYLOAD_MAX 512
#define SLV_JOINCAP_MAC_LEN 32
#define SLV_JOINCAP_FIELDS 8

const char *slv_joincap_reason(slv_joincap_verdict v) {
	switch(v) {
		case SLV_JOINCAP_OK:               return "ok";
		case SLV_JOINCAP_MISSING:          return "cap_missing";
		case SLV_JOINCAP_MALFORMED:        return "cap_malformed";
		case SLV_JOINCAP_BAD_SIGNATURE:    return "cap_bad_signature";
		case SLV_JOINCAP_WRONG_AGENT:      return "cap_wrong_agent";
		case SLV_JOINCAP_WRONG_SESSION:    return "cap_wrong_session";
		case SLV_JOINCAP_WRONG_ROOM:       return "cap_wrong_room";
		case SLV_JOINCAP_EXPIRED:          return "cap_expired";
		case SLV_JOINCAP_REPLAYED:         return "cap_replayed";
		case SLV_JOINCAP_STORE_FULL:       return "cap_replay_store_full";
		case SLV_JOINCAP_STALE_GENERATION: return "cap_stale_generation";
		default:                           return "cap_malformed";
	}
}

/* base64url (RFC 4648 §5) without padding. Returns 1 on success. Rejects '+', '/' and '=' so a
 * standard-base64 capability is malformed rather than half-accepted. */
static int slv_b64url_decode(const char *in, size_t in_len, unsigned char *out, size_t out_cap, size_t *out_len) {
	static const signed char tbl[256] = {
		['A']= 0,['B']= 1,['C']= 2,['D']= 3,['E']= 4,['F']= 5,['G']= 6,['H']= 7,
		['I']= 8,['J']= 9,['K']=10,['L']=11,['M']=12,['N']=13,['O']=14,['P']=15,
		['Q']=16,['R']=17,['S']=18,['T']=19,['U']=20,['V']=21,['W']=22,['X']=23,
		['Y']=24,['Z']=25,['a']=26,['b']=27,['c']=28,['d']=29,['e']=30,['f']=31,
		['g']=32,['h']=33,['i']=34,['j']=35,['k']=36,['l']=37,['m']=38,['n']=39,
		['o']=40,['p']=41,['q']=42,['r']=43,['s']=44,['t']=45,['u']=46,['v']=47,
		['w']=48,['x']=49,['y']=50,['z']=51,['0']=52,['1']=53,['2']=54,['3']=55,
		['4']=56,['5']=57,['6']=58,['7']=59,['8']=60,['9']=61,['-']=62,['_']=63,
	};
	if(in_len == 0 || in_len % 4 == 1)
		return 0;
	size_t produced = 0;
	uint32_t acc = 0;
	int bits = 0;
	for(size_t i = 0; i < in_len; i++) {
		unsigned char c = (unsigned char)in[i];
		/* tbl is zero for every character that is not in the alphabet; 'A' is the only legal zero. */
		if(tbl[c] == 0 && c != 'A')
			return 0;
		acc = (acc << 6) | (uint32_t)tbl[c];
		bits += 6;
		if(bits >= 8) {
			bits -= 8;
			if(produced >= out_cap)
				return 0;
			out[produced++] = (unsigned char)((acc >> bits) & 0xFF);
		}
	}
	/* Leftover bits must be zero padding, never data. */
	if(bits > 0 && ((acc & ((1u << bits) - 1)) != 0))
		return 0;
	*out_len = produced;
	return 1;
}

/* Exactly 16 hex digits into a u64. Zero is legal here (it means "no authority epoch"). */
static int slv_joincap_hex64(const char *s, size_t len, uint64_t *out) {
	if(len != 16)
		return 0;
	uint64_t v = 0;
	for(size_t i = 0; i < len; i++) {
		char c = s[i];
		unsigned d;
		if(c >= '0' && c <= '9')       d = (unsigned)(c - '0');
		else if(c >= 'a' && c <= 'f')  d = (unsigned)(c - 'a' + 10);
		else if(c >= 'A' && c <= 'F')  d = (unsigned)(c - 'A' + 10);
		else return 0;
		v = (v << 4) | d;
	}
	*out = v;
	return 1;
}

/* A field into a fixed identifier buffer: non-empty, no NUL, short enough to store. */
static int slv_joincap_id(const char *s, size_t len, char *out) {
	if(len == 0 || len >= SLV_UUID_LEN)
		return 0;
	memcpy(out, s, len);
	out[len] = '\0';
	return 1;
}

/* A field as a signed 64-bit integer, whole-field or nothing. */
static int slv_joincap_i64(const char *s, size_t len, int64_t *out) {
	char buf[32];
	if(len == 0 || len >= sizeof(buf))
		return 0;
	memcpy(buf, s, len);
	buf[len] = '\0';
	char *end = NULL;
	long long v = strtoll(buf, &end, 10);
	if(end == NULL || *end != '\0')
		return 0;
	*out = (int64_t)v;
	return 1;
}

slv_joincap_verdict slv_joincap_parse(const char *cap, const void *key, size_t key_len, slv_joincap *out) {
	if(cap == NULL || out == NULL || key == NULL || key_len == 0)
		return SLV_JOINCAP_MALFORMED;
	memset(out, 0, sizeof(*out));
	size_t cap_len = strlen(cap);
	if(cap_len == 0 || cap_len > SLV_JOINCAP_MAX_LEN)
		return SLV_JOINCAP_MALFORMED;
	if(strncmp(cap, SLV_JOINCAP_PREFIX, strlen(SLV_JOINCAP_PREFIX)) != 0)
		return SLV_JOINCAP_MALFORMED;
	const char *b64_payload = cap + strlen(SLV_JOINCAP_PREFIX);
	const char *last_dot = strrchr(cap, '.');
	if(last_dot == NULL || last_dot < b64_payload || last_dot == b64_payload)
		return SLV_JOINCAP_MALFORMED;

	/* The signing input is everything before the last dot, taken verbatim from the wire. */
	size_t signing_len = (size_t)(last_dot - cap);
	size_t b64_payload_len = signing_len - strlen(SLV_JOINCAP_PREFIX);
	const char *b64_mac = last_dot + 1;
	size_t b64_mac_len = cap_len - signing_len - 1;

	unsigned char mac_given[SLV_JOINCAP_MAC_LEN];
	size_t mac_given_len = 0;
	if(!slv_b64url_decode(b64_mac, b64_mac_len, mac_given, sizeof(mac_given), &mac_given_len)
			|| mac_given_len != SLV_JOINCAP_MAC_LEN)
		return SLV_JOINCAP_MALFORMED;

	unsigned char mac_want[EVP_MAX_MD_SIZE];
	unsigned int mac_want_len = 0;
	if(HMAC(EVP_sha256(), key, (int)key_len, (const unsigned char *)cap, signing_len,
			mac_want, &mac_want_len) == NULL || mac_want_len != SLV_JOINCAP_MAC_LEN)
		return SLV_JOINCAP_MALFORMED;
	if(CRYPTO_memcmp(mac_given, mac_want, SLV_JOINCAP_MAC_LEN) != 0)
		return SLV_JOINCAP_BAD_SIGNATURE;

	unsigned char payload[SLV_JOINCAP_PAYLOAD_MAX];
	size_t payload_len = 0;
	if(!slv_b64url_decode(b64_payload, b64_payload_len, payload, sizeof(payload) - 1, &payload_len))
		return SLV_JOINCAP_MALFORMED;
	payload[payload_len] = '\0';
	if(memchr(payload, '\0', payload_len) != NULL)
		return SLV_JOINCAP_MALFORMED;   /* an embedded NUL would hide fields from the split below */

	/* Split on '|' into exactly SLV_JOINCAP_FIELDS fields. */
	const char *field[SLV_JOINCAP_FIELDS];
	size_t flen[SLV_JOINCAP_FIELDS];
	int n = 0;
	const char *start = (const char *)payload;
	for(size_t i = 0; i <= payload_len; i++) {
		if(i == payload_len || payload[i] == '|') {
			if(n >= SLV_JOINCAP_FIELDS)
				return SLV_JOINCAP_MALFORMED;
			field[n] = start;
			flen[n] = (size_t)(((const char *)payload + i) - start);
			n++;
			start = (const char *)payload + i + 1;
		}
	}
	if(n != SLV_JOINCAP_FIELDS)
		return SLV_JOINCAP_MALFORMED;

	int64_t room = 0, iat = 0, exp = 0, gen = 0;
	uint64_t epoch = 0;
	if(!slv_joincap_id(field[0], flen[0], out->agent)
			|| !slv_joincap_id(field[1], flen[1], out->session)
			|| !slv_joincap_i64(field[2], flen[2], &room)
			|| !slv_joincap_hex64(field[3], flen[3], &epoch)
			|| !slv_joincap_i64(field[4], flen[4], &gen)
			|| !slv_joincap_i64(field[5], flen[5], &iat)
			|| !slv_joincap_i64(field[6], flen[6], &exp)
			|| !slv_joincap_id(field[7], flen[7], out->nonce))
		return SLV_JOINCAP_MALFORMED;
	if(room <= 0 || gen < 0 || gen > (int64_t)UINT32_MAX || iat <= 0 || exp <= 0 || exp < iat)
		return SLV_JOINCAP_MALFORMED;

	out->room = room;
	out->epoch = epoch;
	out->generation = (uint32_t)gen;
	out->iat = iat;
	out->exp = exp;
	return SLV_JOINCAP_OK;
}

slv_joincap_verdict slv_joincap_check(const slv_joincap *c, const char *display, const char *session_id,
		int64_t room, int64_t now_s, int skew_s) {
	if(c == NULL)
		return SLV_JOINCAP_MALFORMED;
	if(display == NULL || strcmp(c->agent, display) != 0)
		return SLV_JOINCAP_WRONG_AGENT;
	if(session_id == NULL || strcmp(c->session, session_id) != 0)
		return SLV_JOINCAP_WRONG_SESSION;
	if(c->room != room)
		return SLV_JOINCAP_WRONG_ROOM;
	if(now_s > c->exp + (int64_t)skew_s || now_s < c->iat - (int64_t)skew_s)
		return SLV_JOINCAP_EXPIRED;
	return SLV_JOINCAP_OK;
}

int slv_joincap_used_skew(const slv_joincap *c, int64_t now_s, int64_t *out_offset_s) {
	if(c == NULL)
		return 0;
	if(now_s > c->exp) {
		if(out_offset_s) *out_offset_s = now_s - c->exp;   /* the mixer's clock is ahead of the capability */
		return 1;
	}
	if(now_s < c->iat) {
		if(out_offset_s) *out_offset_s = now_s - c->iat;   /* behind: the capability was minted "later" */
		return 1;
	}
	return 0;
}

slv_joincap_verdict slv_joincap_generation(const slv_joincap *c, uint64_t auth_epoch, uint32_t policy_gen) {
	(void)policy_gen;
	if(c == NULL)
		return SLV_JOINCAP_MALFORMED;
	/* §11.5 as the 0.8b amendment restates it (O-96): the epoch is what makes a capability die with its
	 * authority, and ONLY an epoch below the adopted one proves the authority that minted it is gone. An epoch
	 * above it, or a room that has adopted none (auth_epoch 0, which nothing is below), means the mixer has not
	 * caught up — a capability admits a join, it does not adopt an epoch. The generation is not a bound in
	 * either direction: the sim publishes it when ALLOCATED and the mixer holds what it has APPLIED, so ahead
	 * and behind are both ordinary races between minting, sending and joining. */
	if(c->epoch < auth_epoch)
		return SLV_JOINCAP_STALE_GENERATION;
	return SLV_JOINCAP_OK;
}

const char *slv_joincap_gen_note_str(slv_joincap_gen_note n) {
	switch(n) {
		case SLV_JOINCAP_GEN_EPOCH_AHEAD: return "cap_epoch_ahead";
		case SLV_JOINCAP_GEN_AHEAD:       return "cap_generation_ahead";
		case SLV_JOINCAP_GEN_BEHIND:      return "cap_generation_behind";
		case SLV_JOINCAP_GEN_MATCH:       break;
	}
	return "match";
}

slv_joincap_gen_note slv_joincap_generation_note(const slv_joincap *c, uint64_t auth_epoch, uint32_t policy_gen) {
	/* A refused capability says nothing here; the refusal is what gets counted. */
	if(c == NULL || c->epoch < auth_epoch)
		return SLV_JOINCAP_GEN_MATCH;
	if(c->epoch > auth_epoch)
		return SLV_JOINCAP_GEN_EPOCH_AHEAD;
	if(c->generation > policy_gen)
		return SLV_JOINCAP_GEN_AHEAD;
	if(c->generation < policy_gen)
		return SLV_JOINCAP_GEN_BEHIND;
	return SLV_JOINCAP_GEN_MATCH;
}

/* Drop every entry whose window has passed. Order is irrelevant, so the last entry fills the hole. */
static void slv_joincap_nonce_sweep(slv_joincap_nonce_store *st, int64_t now_s) {
	for(int i = 0; i < st->n; ) {
		if(st->entries[i].expires_at <= now_s) {
			st->entries[i] = st->entries[st->n - 1];
			memset(&st->entries[st->n - 1], 0, sizeof(st->entries[0]));
			st->n--;
			st->expired++;
		} else {
			i++;
		}
	}
}

slv_joincap_verdict slv_joincap_nonce_take(slv_joincap_nonce_store *st, const char *nonce,
		int64_t expires_at, int64_t now_s) {
	if(st == NULL || nonce == NULL || *nonce == '\0')
		return SLV_JOINCAP_MALFORMED;
	slv_joincap_nonce_sweep(st, now_s);
	for(int i = 0; i < st->n; i++) {
		if(strcmp(st->entries[i].nonce, nonce) == 0) {
			st->replayed++;
			return SLV_JOINCAP_REPLAYED;
		}
	}
	if(st->n >= SLV_JOINCAP_NONCE_MAX) {
		/* §11.6: refuse rather than admit a join whose replay status cannot be determined. */
		st->full++;
		return SLV_JOINCAP_STORE_FULL;
	}
	size_t len = strlen(nonce);
	if(len >= SLV_UUID_LEN)
		return SLV_JOINCAP_MALFORMED;
	memcpy(st->entries[st->n].nonce, nonce, len + 1);
	st->entries[st->n].expires_at = expires_at;
	st->n++;
	st->taken++;
	return SLV_JOINCAP_OK;
}

int slv_joincap_nonce_count(const slv_joincap_nonce_store *st) {
	return st != NULL ? st->n : 0;
}
