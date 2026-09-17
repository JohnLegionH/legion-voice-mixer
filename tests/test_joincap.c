/*! \file    tests/test_joincap.c
 * \author   Legion Voice Mixer project
 * \copyright GNU General Public License v3
 * \brief    Unit tests for the sim-issued join capability (src/joincap.c, design §11, ledger O-46).
 *
 * \details  Plain C, links OpenSSL and libc only (like test_deferred links nothing), built and run by
 * `make test`. The GOLDEN VECTOR below is the same capability the sim's JoinCapabilityTests mints from the
 * same inputs, so the two implementations are pinned to each other across the two repositories: if either
 * side's canonical string changes, one of the two suites fails.
 *
 * Covers: the golden vector verifies and parses; every malformed shape; a tampered payload and a tampered
 * signature; the wrong key; wrong agent, wrong session, wrong room; the expiry window and both skew edges;
 * replay; the bounded store's eviction and its full refusal; and the O-96 epoch rule with the three
 * accepted-difference counters. Non-zero exit on failure.
 */

#include "../src/joincap.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int g_failures = 0;
static int g_checks = 0;

#define CHECK(cond, msg) do { \
	g_checks++; \
	if(!(cond)) { \
		g_failures++; \
		fprintf(stderr, "  FAIL: %s (%s:%d)\n", (msg), __FILE__, __LINE__); \
	} \
} while(0)

/* ---- the cross-repo golden vector ---------------------------------------------------------------
 * key      "0.4-test-key"
 * payload  "<agent>|<session>|3101|0000018f00000001|7|1789000000|1789000060|<nonce>"
 * Minted identically by Tests/WebRtcJanusService.Tests/JoinCapabilityTests.cs in the sim tree. */
#define KEY      "0.4-test-key"
#define AGENT    "0a000000-0000-4000-8000-00000000000a"
#define SESSION  "5e551000-0000-4000-8000-00000000005e"
#define ROOM     3101
#define EPOCH    0x0000018f00000001ULL
#define GEN      7
#define IAT      1789000000
#define EXP      1789000060
#define NONCE    "b0b1b2b3b4b5b6b7b8b9babbbcbdbebf"
#define GOLDEN \
	"v1.MGEwMDAwMDAtMDAwMC00MDAwLTgwMDAtMDAwMDAwMDAwMDBhfDVlNTUxMDAwLTAwMDAtNDAwMC04MDAwLTAwMDAwMDAwMDA1ZXwz" \
	"MTAxfDAwMDAwMThmMDAwMDAwMDF8N3wxNzg5MDAwMDAwfDE3ODkwMDAwNjB8YjBiMWIyYjNiNGI1YjZiN2I4YjliYWJiYmNiZGJlYmY" \
	".jkvzBWzgK4ZMAuBwoSz-jV1V5_O_pR4UjPdZo6mrNVE"

static slv_joincap_verdict parse(const char *cap, slv_joincap *out) {
	return slv_joincap_parse(cap, KEY, strlen(KEY), out);
}

static void test_golden_vector(void) {
	slv_joincap c;
	CHECK(parse(GOLDEN, &c) == SLV_JOINCAP_OK, "golden: the sim's capability verifies with the shared key");
	CHECK(!strcmp(c.agent, AGENT) && !strcmp(c.session, SESSION) && !strcmp(c.nonce, NONCE),
		"golden: agent, session and nonce parse");
	CHECK(c.room == ROOM && c.epoch == EPOCH && c.generation == GEN && c.iat == IAT && c.exp == EXP,
		"golden: room, epoch, generation and the window parse");
	CHECK(slv_joincap_check(&c, AGENT, SESSION, ROOM, IAT + 1, SLV_JOINCAP_SKEW_S) == SLV_JOINCAP_OK,
		"golden: in context and inside its window");
}

static void test_wrong_key_and_tampering(void) {
	slv_joincap c;
	CHECK(slv_joincap_parse(GOLDEN, "0.4-other-key", 13, &c) == SLV_JOINCAP_BAD_SIGNATURE, "another key: bad signature");
	CHECK(slv_joincap_parse(GOLDEN, NULL, 0, &c) == SLV_JOINCAP_MALFORMED, "no key: malformed, never accepted");

	/* Flip one payload character: the signature no longer matches (it is not a parse error). */
	char tampered[sizeof(GOLDEN) + 4];
	snprintf(tampered, sizeof(tampered), "%s", GOLDEN);
	char *p = strchr(tampered, '.');
	p[5] = (p[5] == 'A') ? 'B' : 'A';
	CHECK(parse(tampered, &c) == SLV_JOINCAP_BAD_SIGNATURE || parse(tampered, &c) == SLV_JOINCAP_MALFORMED,
		"a tampered payload never verifies");

	/* Flip one signature character. */
	snprintf(tampered, sizeof(tampered), "%s", GOLDEN);
	char *last = strrchr(tampered, '.');
	last[1] = (last[1] == 'A') ? 'B' : 'A';
	CHECK(parse(tampered, &c) == SLV_JOINCAP_BAD_SIGNATURE, "a tampered signature is refused");
}

static void test_malformed(void) {
	slv_joincap c;
	const char *bad[] = {
		"",                                   /* empty */
		"v1.",                                /* no signature */
		"v1.abc",                             /* no signature separator */
		"v2.abc.def",                         /* another version */
		"MGEwMDAw.jkvz",                      /* no version prefix */
		"v1..jkvzBWzgK4ZMAuBwoSz-jV1V5_O_pR4UjPdZo6mrNVE",   /* empty payload */
		"v1.MGEwMDAw.",                       /* empty signature */
		"v1.MGEwMDAw+MQ.jkvz",                /* standard base64, not base64url */
		"v1.MGEwMDAw.jkvzBWzgK4ZMAuBwoSz-jV1V5_O_pR4UjPdZo6mrNV",   /* signature not 32 bytes */
	};
	for(size_t i = 0; i < sizeof(bad) / sizeof(bad[0]); i++) {
		slv_joincap_verdict v = parse(bad[i], &c);
		CHECK(v == SLV_JOINCAP_MALFORMED || v == SLV_JOINCAP_BAD_SIGNATURE, "a malformed capability is refused, never parsed");
	}
	CHECK(parse(NULL, &c) == SLV_JOINCAP_MALFORMED, "a NULL capability is malformed");
	CHECK(!strcmp(slv_joincap_reason(SLV_JOINCAP_MISSING), "cap_missing")
		&& !strcmp(slv_joincap_reason(SLV_JOINCAP_EXPIRED), "cap_expired")
		&& !strcmp(slv_joincap_reason(SLV_JOINCAP_REPLAYED), "cap_replayed")
		&& !strcmp(slv_joincap_reason(SLV_JOINCAP_STORE_FULL), "cap_replay_store_full")
		&& !strcmp(slv_joincap_reason(SLV_JOINCAP_STALE_GENERATION), "cap_stale_generation"),
		"every verdict has its own reason string");
}

static void test_context(void) {
	slv_joincap c;
	parse(GOLDEN, &c);
	int64_t now = IAT + 1;
	CHECK(slv_joincap_check(&c, "0b000000-0000-4000-8000-00000000000b", SESSION, ROOM, now, SLV_JOINCAP_SKEW_S)
		== SLV_JOINCAP_WRONG_AGENT, "another agent's join is refused");
	CHECK(slv_joincap_check(&c, AGENT, "99991000-0000-4000-8000-000000000099", ROOM, now, SLV_JOINCAP_SKEW_S)
		== SLV_JOINCAP_WRONG_SESSION, "another viewer session is refused");
	CHECK(slv_joincap_check(&c, AGENT, SESSION, ROOM + 1, now, SLV_JOINCAP_SKEW_S)
		== SLV_JOINCAP_WRONG_ROOM, "another room is refused");
	CHECK(slv_joincap_check(&c, NULL, SESSION, ROOM, now, SLV_JOINCAP_SKEW_S) == SLV_JOINCAP_WRONG_AGENT
		&& slv_joincap_check(&c, AGENT, NULL, ROOM, now, SLV_JOINCAP_SKEW_S) == SLV_JOINCAP_WRONG_SESSION,
		"a join that names neither is refused, not accepted");
}

static void test_window_and_skew(void) {
	slv_joincap c;
	parse(GOLDEN, &c);
	int s = SLV_JOINCAP_SKEW_S;
	int64_t offset = 0;
	CHECK(slv_joincap_check(&c, AGENT, SESSION, ROOM, EXP, s) == SLV_JOINCAP_OK, "at its expiry: accepted");
	CHECK(slv_joincap_check(&c, AGENT, SESSION, ROOM, EXP + s, s) == SLV_JOINCAP_OK,
		"one whole skew window past expiry: still accepted");
	CHECK(slv_joincap_check(&c, AGENT, SESSION, ROOM, EXP + s + 1, s) == SLV_JOINCAP_EXPIRED,
		"one second beyond the tolerance: cap_expired");
	CHECK(slv_joincap_check(&c, AGENT, SESSION, ROOM, IAT - s, s) == SLV_JOINCAP_OK,
		"a mixer clock one skew window behind the sim still accepts");
	CHECK(slv_joincap_check(&c, AGENT, SESSION, ROOM, IAT - s - 1, s) == SLV_JOINCAP_EXPIRED,
		"further behind than the tolerance: cap_expired");
	CHECK(!slv_joincap_used_skew(&c, IAT + 1, &offset), "inside the window, no skew was needed");
	CHECK(slv_joincap_used_skew(&c, EXP + 5, &offset) && offset == 5, "past expiry: the offset is reported ahead");
	CHECK(slv_joincap_used_skew(&c, IAT - 5, &offset) && offset == -5, "before issue: the offset is reported behind");
}

static void test_generation(void) {
	slv_joincap c;
	parse(GOLDEN, &c);

	/* Slice 0.8b (O-96): the generation is NOT a refusal criterion in either direction. The sim publishes a
	 * room's (epoch, generation) when the generation is ALLOCATED, while the mixer holds what it has APPLIED,
	 * so a capability minted between the two is normally AHEAD; a batch that lands between minting and joining
	 * normally leaves it BEHIND. Both are races, not evidence. Only an epoch strictly BELOW the room's adopted
	 * one is provably an older authority, and only that refuses. */
	CHECK(slv_joincap_generation(&c, EPOCH, GEN) == SLV_JOINCAP_OK, "same epoch, same generation: accepted");
	CHECK(slv_joincap_generation(&c, EPOCH, GEN - 1) == SLV_JOINCAP_OK,
		"same epoch, the capability's generation is AHEAD of what the mixer applied: accepted (O-96)");
	CHECK(slv_joincap_generation(&c, EPOCH, GEN + 3) == SLV_JOINCAP_OK,
		"same epoch, the capability's generation is BEHIND what the mixer applied: accepted (O-96)");
	CHECK(slv_joincap_generation(&c, EPOCH + 1, GEN) == SLV_JOINCAP_STALE_GENERATION,
		"an epoch BELOW the room's adopted one is provably an older authority: cap_stale_generation (O-96)");
	CHECK(slv_joincap_generation(&c, EPOCH - 1, GEN) == SLV_JOINCAP_OK,
		"an epoch ABOVE the room's adopted one: accepted, the mixer is the one that is behind (O-96)");
	CHECK(slv_joincap_generation(&c, 0, 0) == SLV_JOINCAP_OK,
		"a room that has adopted no epoch accepts: a capability admits, it does not adopt an epoch (O-96)");

	/* Arming off on both sides: epoch 0 and generation 0 agree. */
	slv_joincap plain = c;
	plain.epoch = 0;
	plain.generation = 0;
	CHECK(slv_joincap_generation(&plain, 0, 0) == SLV_JOINCAP_OK, "arming off on both sides: accepted");
	/* The one asymmetry the rule leaves standing, recorded deliberately: a capability carrying NO epoch (the
	 * sim resolved a room it never armed) is numerically below any adopted epoch, so it is refused. */
	CHECK(slv_joincap_generation(&plain, EPOCH, GEN) == SLV_JOINCAP_STALE_GENERATION,
		"a capability with no epoch at all, against a room that has adopted one: cap_stale_generation (O-96)");
}

/* O-96: what a difference that was ACCEPTED is counted as. The verdict above says nothing about which race
 * happened; this is what the three INFO counters are driven from, and it never turns into a refusal. */
static void test_generation_notes(void) {
	slv_joincap c;
	parse(GOLDEN, &c);
	CHECK(slv_joincap_generation_note(&c, EPOCH, GEN) == SLV_JOINCAP_GEN_MATCH,
		"same epoch, same generation: nothing to count");
	CHECK(slv_joincap_generation_note(&c, EPOCH, GEN - 1) == SLV_JOINCAP_GEN_AHEAD,
		"same epoch, a generation the mixer has not applied yet: cap_generation_ahead");
	CHECK(slv_joincap_generation_note(&c, EPOCH, GEN + 3) == SLV_JOINCAP_GEN_BEHIND,
		"same epoch, a batch landed between minting and joining: cap_generation_behind");
	CHECK(slv_joincap_generation_note(&c, EPOCH - 1, GEN) == SLV_JOINCAP_GEN_EPOCH_AHEAD,
		"an epoch above the adopted one: cap_epoch_ahead");
	CHECK(slv_joincap_generation_note(&c, 0, 0) == SLV_JOINCAP_GEN_EPOCH_AHEAD,
		"a room that has adopted no epoch: cap_epoch_ahead");
	CHECK(slv_joincap_generation_note(&c, EPOCH + 1, GEN) == SLV_JOINCAP_GEN_MATCH,
		"a refused capability counts nothing here: the refusal is what is counted");
	CHECK(!strcmp(slv_joincap_gen_note_str(SLV_JOINCAP_GEN_EPOCH_AHEAD), "cap_epoch_ahead")
		&& !strcmp(slv_joincap_gen_note_str(SLV_JOINCAP_GEN_AHEAD), "cap_generation_ahead")
		&& !strcmp(slv_joincap_gen_note_str(SLV_JOINCAP_GEN_BEHIND), "cap_generation_behind"),
		"the three counter names are what the reports carry");
}

static void test_nonce_store(void) {
	slv_joincap_nonce_store st;
	memset(&st, 0, sizeof(st));
	CHECK(slv_joincap_nonce_take(&st, NONCE, 1000, 100) == SLV_JOINCAP_OK, "a fresh nonce is spent");
	CHECK(slv_joincap_nonce_take(&st, NONCE, 1000, 100) == SLV_JOINCAP_REPLAYED, "the same nonce again: cap_replayed");
	CHECK(st.taken == 1 && st.replayed == 1 && slv_joincap_nonce_count(&st) == 1, "the store counts both");

	/* Past its window the entry is swept, so the same nonce could be spent again — a capability that old is
	 * already refused by the expiry check, which is why the store may forget it. */
	CHECK(slv_joincap_nonce_take(&st, NONCE, 1000, 1001) == SLV_JOINCAP_OK && st.expired == 1,
		"an entry past its window is swept");

	/* Fill it: the next distinct nonce is refused rather than admitted unchecked (§11.6). */
	memset(&st, 0, sizeof(st));
	char nonce[SLV_UUID_LEN];
	for(int i = 0; i < SLV_JOINCAP_NONCE_MAX; i++) {
		snprintf(nonce, sizeof(nonce), "n%08d", i);
		if(slv_joincap_nonce_take(&st, nonce, 1000, 100) != SLV_JOINCAP_OK) {
			CHECK(0, "filling the store to its cap should accept every distinct nonce");
			break;
		}
	}
	CHECK(slv_joincap_nonce_count(&st) == SLV_JOINCAP_NONCE_MAX, "the store holds its cap");
	CHECK(slv_joincap_nonce_take(&st, "one-too-many", 1000, 100) == SLV_JOINCAP_STORE_FULL,
		"a full store refuses rather than admitting a join whose replay status is unknown");
	CHECK(st.full == 1, "the refusal is counted");
	/* Once the window passes, the sweep frees the whole store again. */
	CHECK(slv_joincap_nonce_take(&st, "after-the-window", 2000, 1001) == SLV_JOINCAP_OK,
		"after the window the store recovers");
}

int main(void) {
	test_golden_vector();
	test_wrong_key_and_tampering();
	test_malformed();
	test_context();
	test_window_and_skew();
	test_generation();
	test_generation_notes();
	test_nonce_store();

	printf("test_joincap: %d checks, %d failures\n", g_checks, g_failures);
	return g_failures == 0 ? 0 : 1;
}
