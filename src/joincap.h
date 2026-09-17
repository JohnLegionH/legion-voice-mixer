/*! \file    joincap.h
 * \author   Legion Voice Mixer project
 * \copyright GNU General Public License v3
 * \brief    The sim-issued join capability: parse, verify, context and replay checks
 *           (docs/voice/nonspatial-phase0-design.md §11, ledger O-46).
 *
 * \details  A capability is minted by the sim for one join and verified here:
 *
 *   join_cap = "v1." + b64url(payload) + "." + b64url(HMAC-SHA256(key, "v1." + b64url(payload)))
 *   payload  = "<agent>|<session>|<room>|<epoch>|<generation>|<iat>|<exp>|<nonce>"
 *
 * The viewer never sees it: the sim's own Janus session performs the join, so the capability travels
 * sim -> mixer only. It is defence in depth against a leaked JS_API_SECRET (§11.1); it is NOT arming,
 * and it grants no audibility (§11.5).
 *
 * This module links OpenSSL (HMAC-SHA256, constant-time compare) and libc, no Janus and no glib, so it
 * compiles into the plugin .so and into tests/test_joincap.c (`make test`).
 *
 * NEVER log a capability, its payload or its nonce. Callers log the verdict's reason string only.
 */

#ifndef SLV_JOINCAP_H
#define SLV_JOINCAP_H

#include <stddef.h>
#include <stdint.h>

/*! \brief Longest capability string accepted; anything longer is malformed without parsing. */
#define SLV_JOINCAP_MAX_LEN 1024

/*! \brief Identifier field size (agent, session, nonce), matching the mixer's display buffers. */
#ifndef SLV_UUID_LEN
#define SLV_UUID_LEN 40
#endif

/*! \brief Live nonces remembered per mixer process (§11.6). One 60 s lifetime holds far fewer. */
#define SLV_JOINCAP_NONCE_MAX 4096

/*! \brief Clock skew tolerated in either direction, seconds (§11.7). */
#define SLV_JOINCAP_SKEW_S 120

/*! \brief The wire version prefix this mixer accepts. */
#define SLV_JOINCAP_PREFIX "v1."

/*! \brief Verdicts, in the order §11.4 evaluates them. The reason strings are what a reply carries. */
typedef enum slv_joincap_verdict {
	SLV_JOINCAP_OK = 0,
	SLV_JOINCAP_MISSING,            /*!< required, and the join carried none */
	SLV_JOINCAP_MALFORMED,          /*!< shape, base64url, field count or number parsing */
	SLV_JOINCAP_BAD_SIGNATURE,      /*!< HMAC mismatch */
	SLV_JOINCAP_WRONG_AGENT,        /*!< payload agent is not the join's display */
	SLV_JOINCAP_WRONG_SESSION,      /*!< payload session is not the join's session_id */
	SLV_JOINCAP_WRONG_ROOM,         /*!< payload room is not the join's room */
	SLV_JOINCAP_EXPIRED,            /*!< outside [iat - skew, exp + skew] */
	SLV_JOINCAP_REPLAYED,           /*!< this nonce was already spent */
	SLV_JOINCAP_STORE_FULL,         /*!< replay cannot be ruled out: the nonce store is full */
	SLV_JOINCAP_STALE_GENERATION,   /*!< an epoch BELOW the room's adopted one: provably an older authority */
} slv_joincap_verdict;

/*! \brief The reason string for a verdict ("ok", "cap_missing", ...). Never NULL. */
const char *slv_joincap_reason(slv_joincap_verdict v);

/*! \brief One parsed capability. Strings are NUL-terminated; nothing here may be logged. */
typedef struct slv_joincap {
	char agent[SLV_UUID_LEN];
	char session[SLV_UUID_LEN];
	int64_t room;
	uint64_t epoch;        /*!< the sim authority's room_epoch at issue; 0 = none (arming off) */
	uint32_t generation;   /*!< that room's policy_generation at issue; 0 = none */
	int64_t iat;
	int64_t exp;
	char nonce[SLV_UUID_LEN];
} slv_joincap;

/*! \brief Parse and verify the signature of \c cap with \c key. Fills \c out on OK. Returns
 * SLV_JOINCAP_MALFORMED or SLV_JOINCAP_BAD_SIGNATURE otherwise. A NULL/empty cap or key is malformed;
 * the caller decides whether a missing capability is SLV_JOINCAP_MISSING. */
slv_joincap_verdict slv_joincap_parse(const char *cap, const void *key, size_t key_len, slv_joincap *out);

/*! \brief The context and time checks (§11.4 rows 4-7): the capability must name this join's agent,
 * session and room, and \c now_s must lie within [iat - skew, exp + skew]. */
slv_joincap_verdict slv_joincap_check(const slv_joincap *c, const char *display, const char *session_id,
	int64_t room, int64_t now_s, int skew_s);

/*! \brief TRUE (1) when the capability was accepted only because of the skew tolerance, so the caller can
 * WARN with the observed offset. \c out_offset_s is signed: positive means the capability's clock ran ahead. */
int slv_joincap_used_skew(const slv_joincap *c, int64_t now_s, int64_t *out_offset_s);

/*! \brief The epoch check (§11.5, restated by the 0.8b amendment, ledger O-96): the capability's
 * (epoch, generation) against the room's adopted authority.
 *
 * The GENERATION never refuses. The sim publishes a room's generation when it is ALLOCATED
 * (`VisAuthority.NextGeneration`), while the mixer holds the highest it has APPLIED, so a capability minted
 * between those two moments is legitimately AHEAD — as is one for a room whose batch was dropped or is in
 * flight, for a fresh room, and for a room re-created after a grace destroy. A batch that lands between
 * minting and joining leaves it legitimately BEHIND. Both directions are ordinary races; neither is evidence,
 * and an upper bound on the generation stops no attack that the HMAC, the nonce, the expiry and the
 * agent/session/room/epoch binding do not already stop.
 *
 * So: SLV_JOINCAP_STALE_GENERATION ONLY when the capability's epoch is strictly LOWER than \c auth_epoch,
 * the one case that proves an older authority minted it. A room that has adopted no epoch (\c auth_epoch 0)
 * accepts: a capability ADMITS a join, it does not adopt an epoch. Counted, not refused, while the knob is off. */
slv_joincap_verdict slv_joincap_generation(const slv_joincap *c, uint64_t auth_epoch, uint32_t policy_gen);

/*! \brief Why an ACCEPTED capability's (epoch, generation) differed from the room's, so the caller can count
 * and report the race. Never a refusal: these are counter names, not verdicts. */
typedef enum slv_joincap_gen_note {
	SLV_JOINCAP_GEN_MATCH = 0,     /*!< the room's own (epoch, generation) — or a capability that was refused */
	SLV_JOINCAP_GEN_EPOCH_AHEAD,   /*!< a higher epoch than the room has adopted (including: it has adopted none) */
	SLV_JOINCAP_GEN_AHEAD,         /*!< same epoch, a generation above what the mixer has applied */
	SLV_JOINCAP_GEN_BEHIND,        /*!< same epoch, a generation below what the mixer has applied */
} slv_joincap_gen_note;

/*! \brief The counter name for a note ("cap_epoch_ahead", "cap_generation_ahead", "cap_generation_behind").
 * Never NULL; SLV_JOINCAP_GEN_MATCH is "match". */
const char *slv_joincap_gen_note_str(slv_joincap_gen_note n);

/*! \brief Classify an accepted capability's difference from the room (O-96). Pure; it changes no verdict. A
 * capability slv_joincap_generation() refuses classifies as SLV_JOINCAP_GEN_MATCH: its refusal is the count. */
slv_joincap_gen_note slv_joincap_generation_note(const slv_joincap *c, uint64_t auth_epoch, uint32_t policy_gen);

/*! \brief The bounded replay store (§11.6). Zero-initialised is empty and ready. NOT thread-safe: the
 * plugin calls it under its own lock. */
typedef struct slv_joincap_nonce_store {
	struct {
		char nonce[SLV_UUID_LEN];
		int64_t expires_at;   /*!< the capability's exp + skew; the entry is dropped past this */
	} entries[SLV_JOINCAP_NONCE_MAX];
	int n;
	/* Cumulative counters, for query_session. */
	uint64_t taken;       /*!< nonces accepted and remembered */
	uint64_t replayed;    /*!< nonces seen a second time */
	uint64_t full;        /*!< refusals because the store was full */
	uint64_t expired;     /*!< entries dropped because their window had passed */
} slv_joincap_nonce_store;

/*! \brief Spend a nonce: OK the first time, SLV_JOINCAP_REPLAYED after that, SLV_JOINCAP_STORE_FULL when
 * the store is full of live entries (fail closed: replay cannot be ruled out). Expired entries are swept
 * first, so a full store is genuinely full of live nonces. */
slv_joincap_verdict slv_joincap_nonce_take(slv_joincap_nonce_store *st, const char *nonce,
	int64_t expires_at, int64_t now_s);

/*! \brief Live entries (diagnostics). */
int slv_joincap_nonce_count(const slv_joincap_nonce_store *st);

#endif /* SLV_JOINCAP_H */
