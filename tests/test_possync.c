/* Copyright (c) 2026 Legion Builds
 * SPDX-License-Identifier: GPL-3.0-only
 * Licensed under the GNU General Public License, version 3 only; see the LICENSE file in the repository root.
 */
/*! \file    tests/test_possync.c
 * \author   Legion Voice Mixer project
 * \copyright GNU General Public License v3
 * \brief    Unit tests for the peer_pos parser, the per-room store and the observe counters (src/possync.c).
 *
 * \details  Plain C, links jansson and libm only (no Janus). Built and run by `make test`. Covers the request
 * envelope (each required field, the wrong type of each), per-entry bounds (coordinates, speed, tolerance, UUID
 * shape, array length, non-integers), the size and entry caps, skip-and-count of a bad entry, the store (full
 * replaces, partial updates, case-insensitive lookup, capacity), the age arithmetic, and each observe counter:
 * would-clamp with the largest deviation, would-use-sim, and no-sim (missing and stale). Non-zero exit on failure.
 */

#include "../src/possync.h"

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

#define U1 "11111111-1111-4111-8111-111111111111"
#define U2 "22222222-2222-4222-8222-222222222222"
#define U3 "33333333-3333-4333-8333-333333333333"

static slv_pos_status parse(const char *json, slv_posbatch *out) {
	return slv_posbatch_parse(json, strlen(json), out);
}

/* A request with the given pos object body. */
static slv_pos_status parse_pos(const char *pos_body, slv_posbatch *out) {
	char buf[4096];
	snprintf(buf, sizeof(buf), "{\"request\":\"peer_pos\",\"room\":7,\"seq\":1760000000001,\"full\":true,"
		"\"mode\":\"observe\",\"pos\":{%s}}", pos_body);
	return parse(buf, out);
}

static void test_valid(void) {
	slv_posbatch b;
	slv_pos_status st = parse(
		"{\"request\":\"peer_pos\",\"room\":900000001,\"seq\":1760000000001,\"full\":true,\"mode\":\"observe\","
		"\"room_epoch\":\"00000000000000ab\",\"pos\":{\"" U1 "\":[25612800,25612800,2200,800,500],"
		"\"" U2 "\":[-5,0,7,6000,2500]}}", &b);
	CHECK(st == SLV_POS_OK, "a request as the sim sends it parses");
	CHECK(b.room == 900000001 && b.seq == 1760000000001LL, "room and the 13-digit seq are read whole");
	CHECK(b.full == 1 && !b.enforce_requested, "full true, observe mode");
	CHECK(b.has_epoch && b.room_epoch == 0xabULL, "room_epoch read");
	CHECK(b.n_entries == 2 && b.n_skipped == 0, "two entries, none skipped");
	int found1 = 0, found2 = 0;
	for(int i = 0; i < b.n_entries; i++) {
		const slv_pos_entry *e = &b.entries[i];
		if(!strcmp(e->uuid, U1))
			found1 = e->x == 25612800 && e->y == 25612800 && e->z == 2200 && e->speed_cms == 800 && e->tol_cm == 500;
		if(!strcmp(e->uuid, U2))
			found2 = e->x == -5 && e->y == 0 && e->z == 7 && e->speed_cms == 6000 && e->tol_cm == 2500;
	}
	CHECK(found1 && found2, "every field of both entries is read, a negative coordinate included");
	slv_posbatch_free(&b);

	st = parse("{\"room\":1,\"seq\":2,\"full\":false,\"pos\":{}}", &b);
	CHECK(st == SLV_POS_OK && b.n_entries == 0 && !b.full && !b.has_epoch,
		"an empty pos object is valid: the room has emptied (no mode, no epoch needed)");
	slv_posbatch_free(&b);

	st = parse("{\"room\":1,\"seq\":2,\"full\":true,\"mode\":\"enforce\",\"pos\":{}}", &b);
	CHECK(st == SLV_POS_OK && b.enforce_requested, "mode enforce is read, and only flagged");
	slv_posbatch_free(&b);
	st = parse("{\"room\":1,\"seq\":2,\"full\":true,\"mode\":\"somethingelse\",\"pos\":{}}", &b);
	CHECK(st == SLV_POS_OK && !b.enforce_requested, "an unknown mode word is ignored");
	slv_posbatch_free(&b);
}

static void test_envelope(void) {
	slv_posbatch b;
	CHECK(parse("not json", &b) == SLV_POS_MALFORMED, "not JSON is malformed");
	CHECK(parse("[1,2]", &b) == SLV_POS_MALFORMED, "a JSON array is malformed");
	CHECK(slv_posbatch_parse(NULL, 0, &b) == SLV_POS_MALFORMED, "a NULL buffer is malformed");
	CHECK(slv_posbatch_parse("{}", 0, &b) == SLV_POS_MALFORMED, "a zero length is malformed");
	CHECK(parse("{\"seq\":1,\"full\":true,\"pos\":{}}", &b) == SLV_POS_MALFORMED, "no room");
	CHECK(parse("{\"room\":1,\"full\":true,\"pos\":{}}", &b) == SLV_POS_MALFORMED, "no seq");
	CHECK(parse("{\"room\":1,\"seq\":1,\"pos\":{}}", &b) == SLV_POS_MALFORMED, "no full");
	CHECK(parse("{\"room\":1,\"seq\":1,\"full\":true}", &b) == SLV_POS_MALFORMED, "no pos");
	CHECK(parse("{\"room\":\"1\",\"seq\":1,\"full\":true,\"pos\":{}}", &b) == SLV_POS_MALFORMED, "room as a string");
	CHECK(parse("{\"room\":1.5,\"seq\":1,\"full\":true,\"pos\":{}}", &b) == SLV_POS_MALFORMED, "room as a real");
	CHECK(parse("{\"room\":-1,\"seq\":1,\"full\":true,\"pos\":{}}", &b) == SLV_POS_MALFORMED, "a negative room");
	CHECK(parse("{\"room\":1,\"seq\":0,\"full\":true,\"pos\":{}}", &b) == SLV_POS_MALFORMED, "seq 0 (it starts at 1)");
	CHECK(parse("{\"room\":1,\"seq\":-3,\"full\":true,\"pos\":{}}", &b) == SLV_POS_MALFORMED, "a negative seq");
	CHECK(parse("{\"room\":1,\"seq\":\"2\",\"full\":true,\"pos\":{}}", &b) == SLV_POS_MALFORMED, "seq as a string");
	CHECK(parse("{\"room\":1,\"seq\":1,\"full\":1,\"pos\":{}}", &b) == SLV_POS_MALFORMED, "full as an integer");
	CHECK(parse("{\"room\":1,\"seq\":1,\"full\":\"true\",\"pos\":{}}", &b) == SLV_POS_MALFORMED, "full as a string");
	CHECK(parse("{\"room\":1,\"seq\":1,\"full\":true,\"pos\":[]}", &b) == SLV_POS_MALFORMED, "pos as an array");
	CHECK(parse("{\"room\":1,\"seq\":1,\"full\":true,\"pos\":null}", &b) == SLV_POS_MALFORMED, "pos null");
	CHECK(parse("{\"room\":1,\"seq\":1,\"full\":true,\"mode\":3,\"pos\":{}}", &b) == SLV_POS_MALFORMED, "mode as an integer");
	CHECK(parse("{\"room\":1,\"seq\":1,\"full\":true,\"room_epoch\":\"abc\",\"pos\":{}}", &b) == SLV_POS_MALFORMED,
		"a short room_epoch (it is exactly 16 hex digits, as for peer_ctl_batch)");
	CHECK(parse("{\"room\":1,\"seq\":1,\"full\":true,\"room_epoch\":\"0000000000000000\",\"pos\":{}}", &b) == SLV_POS_MALFORMED,
		"an all-zero room_epoch (zero means none)");
	CHECK(parse("{\"room\":1,\"seq\":1,\"full\":true,\"room_epoch\":12,\"pos\":{}}", &b) == SLV_POS_MALFORMED,
		"room_epoch as an integer");
	CHECK(b.entries == NULL && b.n_entries == 0, "a rejected request leaves nothing allocated");
}

static void test_entry_bounds(void) {
	slv_posbatch b;
	CHECK(parse_pos("\"" U1 "\":[0,0,0,0,0]", &b) == SLV_POS_OK && b.n_entries == 1, "all zeros is valid");
	slv_posbatch_free(&b);
	CHECK(parse_pos("\"" U1 "\":[2147483647,-2147483647,2147483647,200000,500000]", &b) == SLV_POS_OK && b.n_entries == 1,
		"every field at its limit is valid");
	slv_posbatch_free(&b);
	CHECK(parse_pos("\"" U1 "\":[2147483648,0,0,0,0]", &b) == SLV_POS_OK && b.n_entries == 0 && b.n_skipped == 1,
		"x above the limit is skipped");
	slv_posbatch_free(&b);
	CHECK(parse_pos("\"" U1 "\":[0,-2147483648,0,0,0]", &b) == SLV_POS_OK && b.n_entries == 0 && b.n_skipped == 1,
		"y below the limit is skipped");
	slv_posbatch_free(&b);
	CHECK(parse_pos("\"" U1 "\":[0,0,99999999999,0,0]", &b) == SLV_POS_OK && b.n_skipped == 1, "z far out is skipped");
	slv_posbatch_free(&b);
	CHECK(parse_pos("\"" U1 "\":[0,0,0,-1,0]", &b) == SLV_POS_OK && b.n_skipped == 1, "a negative speed is skipped");
	slv_posbatch_free(&b);
	CHECK(parse_pos("\"" U1 "\":[0,0,0,200001,0]", &b) == SLV_POS_OK && b.n_skipped == 1, "a speed over 2 km/s is skipped");
	slv_posbatch_free(&b);
	CHECK(parse_pos("\"" U1 "\":[0,0,0,0,-1]", &b) == SLV_POS_OK && b.n_skipped == 1, "a negative tolerance is skipped");
	slv_posbatch_free(&b);
	CHECK(parse_pos("\"" U1 "\":[0,0,0,0,500001]", &b) == SLV_POS_OK && b.n_skipped == 1, "a tolerance over 5 km is skipped");
	slv_posbatch_free(&b);
	CHECK(parse_pos("\"" U1 "\":[1.5,0,0,0,0]", &b) == SLV_POS_OK && b.n_skipped == 1, "a real coordinate is skipped");
	slv_posbatch_free(&b);
	CHECK(parse_pos("\"" U1 "\":[0,0,0,0.0,0]", &b) == SLV_POS_OK && b.n_skipped == 1, "a real speed is skipped");
	slv_posbatch_free(&b);
	CHECK(parse_pos("\"" U1 "\":[\"1\",0,0,0,0]", &b) == SLV_POS_OK && b.n_skipped == 1, "a string coordinate is skipped");
	slv_posbatch_free(&b);
	CHECK(parse_pos("\"" U1 "\":[true,0,0,0,0]", &b) == SLV_POS_OK && b.n_skipped == 1, "a boolean coordinate is skipped");
	slv_posbatch_free(&b);
	CHECK(parse_pos("\"" U1 "\":[null,0,0,0,0]", &b) == SLV_POS_OK && b.n_skipped == 1, "a null coordinate is skipped");
	slv_posbatch_free(&b);
	CHECK(parse_pos("\"" U1 "\":[0,0,0,0]", &b) == SLV_POS_OK && b.n_skipped == 1, "four members are skipped");
	slv_posbatch_free(&b);
	CHECK(parse_pos("\"" U1 "\":[0,0,0,0,0,9]", &b) == SLV_POS_OK && b.n_entries == 1,
		"a sixth member is ignored, as SLData ignores extra array members");
	slv_posbatch_free(&b);
	CHECK(parse_pos("\"" U1 "\":{\"x\":0,\"y\":0,\"z\":0}", &b) == SLV_POS_OK && b.n_skipped == 1,
		"an object in place of the array is skipped");
	slv_posbatch_free(&b);
	CHECK(parse_pos("\"not-a-uuid\":[0,0,0,0,0]", &b) == SLV_POS_OK && b.n_skipped == 1, "a bad UUID is skipped");
	slv_posbatch_free(&b);
	CHECK(parse_pos("\"11111111111141118111111111111111\":[0,0,0,0,0]", &b) == SLV_POS_OK && b.n_skipped == 1,
		"a UUID without dashes is skipped");
	slv_posbatch_free(&b);
	CHECK(parse_pos("\"1111111g-1111-4111-8111-111111111111\":[0,0,0,0,0]", &b) == SLV_POS_OK && b.n_skipped == 1,
		"a UUID with a non-hex digit is skipped");
	slv_posbatch_free(&b);
	CHECK(parse_pos("\"\":[0,0,0,0,0]", &b) == SLV_POS_OK && b.n_skipped == 1, "an empty key is skipped");
	slv_posbatch_free(&b);
	CHECK(parse_pos("\"AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE\":[1,2,3,4,5]", &b) == SLV_POS_OK && b.n_entries == 1
		&& !strcmp(b.entries[0].uuid, "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"), "an upper-case UUID is stored lower-case");
	slv_posbatch_free(&b);
}

static void test_skip_and_count(void) {
	slv_posbatch b;
	slv_pos_status st = parse_pos("\"" U1 "\":[1,2,3,4,5],\"bad\":[1,2,3,4,5],\"" U2 "\":[1,2,3,-4,5],\"" U3 "\":[9,9,9,9,9]", &b);
	CHECK(st == SLV_POS_OK && b.n_entries == 2 && b.n_skipped == 2, "two bad entries are skipped, the two good ones kept");
	slv_posbatch_free(&b);
}

static void test_size_caps(void) {
	slv_posbatch b;
	/* 128 entries is accepted; 129 is refused whole. */
	char *buf = malloc(32768);
	for(int n = 128; n <= 129; n++) {
		int off = snprintf(buf, 32768, "{\"room\":1,\"seq\":5,\"full\":true,\"pos\":{");
		for(int i = 0; i < n; i++)
			off += snprintf(buf + off, 32768 - (size_t)off, "%s\"%08x-0000-4000-8000-000000000000\":[%d,0,0,0,0]",
				i ? "," : "", (unsigned)(i + 1), i);
		snprintf(buf + off, 32768 - (size_t)off, "}}");
		slv_pos_status st = parse(buf, &b);
		if(n == 128) {
			CHECK(st == SLV_POS_OK && b.n_entries == 128, "128 entries are accepted");
			slv_posbatch_free(&b);
		} else {
			CHECK(st == SLV_POS_TOOBIG && b.entries == NULL, "129 entries are refused whole (too_big)");
		}
	}
	/* The sim's full room of 110 is about 9 KB. */
	{
		int off = snprintf(buf, 32768, "{\"request\":\"peer_pos\",\"room\":1,\"seq\":1760000000002,\"full\":true,\"mode\":\"observe\",\"pos\":{");
		for(int i = 0; i < 110; i++)
			off += snprintf(buf + off, 32768 - (size_t)off, "%s\"%08x-0000-4000-8000-000000000000\":[25612800,25612800,2200,800,500]",
				i ? "," : "", (unsigned)(i + 1));
		snprintf(buf + off, 32768 - (size_t)off, "}}");
		CHECK(strlen(buf) < SLV_POS_MAX_BYTES / 4, "a room of 110 is well under the byte cap");
		slv_pos_status st = parse(buf, &b);
		CHECK(st == SLV_POS_OK && b.n_entries == 110 && b.n_skipped == 0, "a room of 110 parses whole");
		slv_posbatch_free(&b);
	}
	free(buf);
	/* Over the byte cap: refused before parsing, even when the content would be valid. */
	size_t big = SLV_POS_MAX_BYTES + 10;
	char *huge = malloc(big + 1);
	memset(huge, ' ', big);
	huge[big] = '\0';
	const char *head = "{\"room\":1,\"seq\":1,\"full\":true,\"pos\":{}}";
	memcpy(huge, head, strlen(head));
	CHECK(slv_posbatch_parse(huge, big, &b) == SLV_POS_TOOBIG, "a body over 64 KB is refused whole");
	CHECK(slv_posbatch_parse(huge, SLV_POS_MAX_BYTES, &b) == SLV_POS_OK, "a body of exactly 64 KB is parsed");
	slv_posbatch_free(&b);
	free(huge);
	/* Not NUL-terminated: the length is honoured. */
	const char *two = "{\"room\":1,\"seq\":1,\"full\":true,\"pos\":{}}GARBAGE";
	CHECK(slv_posbatch_parse(two, strlen(two) - strlen("GARBAGE"), &b) == SLV_POS_OK, "bytes past len are not read");
	slv_posbatch_free(&b);
}

static slv_posbatch make(int full, const char *pos_body) {
	slv_posbatch b;
	char buf[4096];
	snprintf(buf, sizeof(buf), "{\"room\":1,\"seq\":3,\"full\":%s,\"pos\":{%s}}", full ? "true" : "false", pos_body);
	slv_pos_status st = parse(buf, &b);
	if(st != SLV_POS_OK) {
		fprintf(stderr, "test setup: request did not parse\n");
		exit(2);
	}
	return b;
}

static slv_pos_store g_store;

static void test_store(void) {
	slv_pos_store_clear(&g_store);
	slv_posbatch b = make(1, "\"" U1 "\":[100,200,300,800,500],\"" U2 "\":[1,2,3,4,5]");
	int dropped = -1;
	CHECK(slv_pos_store_apply(&g_store, &b, 1000000, &dropped) == 2 && dropped == 0 && g_store.count == 2, "two stored");
	slv_posbatch_free(&b);
	const slv_pos_sim *s = slv_pos_store_find(&g_store, U1);
	CHECK(s != NULL && s->e.x == 100 && s->e.y == 200 && s->e.z == 300 && s->e.speed_cms == 800 && s->e.tol_cm == 500
		&& s->recv_us == 1000000, "a stored point reads back with its receipt time");
	CHECK(slv_pos_store_find(&g_store, "11111111-1111-4111-8111-111111111111") == s, "found by an equal string");
	CHECK(slv_pos_store_find(&g_store, "AAAAAAAA-0000-4000-8000-000000000000") == NULL, "an absent UUID is NULL");
	CHECK(slv_pos_store_find(&g_store, NULL) == NULL && slv_pos_store_find(&g_store, "") == NULL, "NULL and empty are NULL");

	/* Case-insensitive lookup. */
	slv_posbatch b2 = make(1, "\"AAAAAAAA-0000-4000-8000-000000000000\":[7,8,9,1,2]");
	slv_pos_store_apply(&g_store, &b2, 2000000, &dropped);
	slv_posbatch_free(&b2);
	CHECK(slv_pos_store_find(&g_store, "aaaaaaaa-0000-4000-8000-000000000000") != NULL, "a request in upper case is found in lower");
	CHECK(slv_pos_store_find(&g_store, "AaAaAaAa-0000-4000-8000-000000000000") != NULL, "a lookup in mixed case is found");
	CHECK(slv_pos_store_find(&g_store, U1) == NULL, "a full request replaced the store: U1 is gone");

	/* Partial update touches only the named participants. */
	slv_pos_store_clear(&g_store);
	b = make(1, "\"" U1 "\":[1,1,1,1,1],\"" U2 "\":[2,2,2,2,2]");
	slv_pos_store_apply(&g_store, &b, 10, &dropped);
	slv_posbatch_free(&b);
	b = make(0, "\"" U2 "\":[9,9,9,9,9],\"" U3 "\":[3,3,3,3,3]");
	slv_pos_store_apply(&g_store, &b, 20, &dropped);
	slv_posbatch_free(&b);
	CHECK(g_store.count == 3, "a partial request adds U3 and keeps U1");
	const slv_pos_sim *s1 = slv_pos_store_find(&g_store, U1), *s2 = slv_pos_store_find(&g_store, U2);
	CHECK(s1 && s1->e.x == 1 && s1->recv_us == 10, "U1 is untouched, with its old receipt time");
	CHECK(s2 && s2->e.x == 9 && s2->recv_us == 20, "U2 is updated, with the new receipt time");
	CHECK(g_store.count == 3 && slv_pos_store_find(&g_store, U3) != NULL, "U3 added");

	/* An empty full request empties the room. */
	b = make(1, "");
	CHECK(slv_pos_store_apply(&g_store, &b, 30, &dropped) == 0 && g_store.count == 0, "an empty full request empties the store");
	slv_posbatch_free(&b);
	CHECK(slv_pos_store_find(&g_store, U1) == NULL, "and nothing is found");
}

static void test_store_capacity(void) {
	slv_pos_store_clear(&g_store);
	slv_posbatch b;
	memset(&b, 0, sizeof(b));
	int total = SLV_POS_STORE_MAX + 10;
	b.entries = calloc((size_t)total, sizeof(slv_pos_entry));
	b.n_entries = total;
	b.full = 1;
	for(int i = 0; i < total; i++) {
		snprintf(b.entries[i].uuid, SLV_UUID_LEN, "%08x-0000-4000-8000-000000000000", (unsigned)(i + 1));
		b.entries[i].x = i;
	}
	int dropped = 0;
	int stored = slv_pos_store_apply(&g_store, &b, 5, &dropped);
	CHECK(stored == SLV_POS_STORE_MAX && dropped == 10 && g_store.count == SLV_POS_STORE_MAX,
		"the store takes SLV_POS_STORE_MAX participants and counts the rest as dropped");
	CHECK(slv_pos_store_find(&g_store, "00000001-0000-4000-8000-000000000000") != NULL, "the first is held");
	/* Updating a held participant when full still works. */
	b.full = 0;
	b.n_entries = 1;
	b.entries[0].x = 4242;
	CHECK(slv_pos_store_apply(&g_store, &b, 6, &dropped) == 1 && dropped == 0, "an update of a held participant works when full");
	const slv_pos_sim *s = slv_pos_store_find(&g_store, "00000001-0000-4000-8000-000000000000");
	CHECK(s && s->e.x == 4242 && s->recv_us == 6, "and it took");
	free(b.entries);
}

static void test_tolerance_and_age(void) {
	slv_pos_sim sim;
	memset(&sim, 0, sizeof(sim));
	sim.e.tol_cm = 500;
	sim.e.speed_cms = 800;
	CHECK(slv_pos_tolerance_now(&sim, 0) == 500.0, "age 0: the tolerance as sent");
	CHECK(slv_pos_tolerance_now(&sim, 1000000) == 1300.0, "age 1 s: tolerance + speed");
	CHECK(slv_pos_tolerance_now(&sim, 5000000) == 4500.0, "age 5 s: tolerance + 5 x speed");
	CHECK(slv_pos_tolerance_now(&sim, 250000) == 700.0, "age 250 ms");
	CHECK(slv_pos_tolerance_now(&sim, -5) == 500.0, "a negative age (clock step) counts as 0");
}

static slv_pos_sim fresh(int64_t x, int64_t y, int64_t z, int64_t speed, int64_t tol, int64_t recv_us) {
	slv_pos_sim s;
	memset(&s, 0, sizeof(s));
	s.used = 1;
	s.e.x = x; s.e.y = y; s.e.z = z; s.e.speed_cms = speed; s.e.tol_cm = tol;
	s.recv_us = recv_us;
	return s;
}

static void test_observe_would_clamp(void) {
	slv_pos_part_stats ps;
	slv_pos_room_stats rs;
	memset(&ps, 0, sizeof(ps));
	memset(&rs, 0, sizeof(rs));
	slv_pos_sim sim = fresh(10000, 20000, 3000, 800, 500, 0);
	double on[3] = { 10000, 20000, 3000 };
	slv_pos_room_tick_begin(&rs);
	slv_pos_observe(&ps, &rs, &sim, 0, 1, 1, on);
	CHECK(ps.state == SLV_POS_SIM_FRESH && ps.would_clamp == 0 && ps.dev_cm == 0.0 && rs.would_clamp_ticks == 0,
		"a claim on the sim point: no would-clamp");
	double inside[3] = { 10000 + 500, 20000, 3000 };
	slv_pos_observe(&ps, &rs, &sim, 0, 1, 1, inside);
	CHECK(ps.would_clamp == 0 && ps.dev_cm == 500.0, "a claim exactly on the tolerance boundary: not beyond it");
	double just_out[3] = { 10000 + 501, 20000, 3000 };
	slv_pos_observe(&ps, &rs, &sim, 0, 1, 1, just_out);
	CHECK(ps.would_clamp == 1 && rs.would_clamp_ticks == 1 && rs.would_clamp_now == 1, "one centimetre beyond: counted");
	CHECK(ps.would_clamp_max_dev_cm == 501.0 && ps.would_clamp_max_excess_cm == 1.0, "largest deviation and excess recorded");
	double far[3] = { 10000 + 30000, 20000 + 40000, 3000 };
	slv_pos_observe(&ps, &rs, &sim, 0, 1, 1, far);
	CHECK(ps.would_clamp == 2 && ps.would_clamp_max_dev_cm == 50000.0 && ps.would_clamp_max_excess_cm == 49500.0,
		"a claim 500 m away: counted, and the maximum rises");
	slv_pos_observe(&ps, &rs, &sim, 0, 1, 1, just_out);
	CHECK(ps.would_clamp == 3 && ps.would_clamp_max_dev_cm == 50000.0, "a smaller later one does not lower the maximum");
	CHECK(rs.would_clamp_max_dev_cm == 50000.0 && rs.would_clamp_max_excess_cm == 49500.0, "the room keeps the maximum too");
	CHECK(ps.would_use_sim == 0 && ps.no_sim == 0 && rs.no_sim_ticks == 0 && rs.would_use_sim_ticks == 0,
		"nothing else was counted");
	double up[3] = { 10000, 20000, 3000 + 600 };
	slv_pos_observe(&ps, &rs, &sim, 0, 1, 1, up);
	CHECK(ps.would_clamp == 4, "the vertical distance counts too (3-D)");
	slv_pos_room_tick_begin(&rs);
	CHECK(rs.would_clamp_now == 0 && rs.would_clamp_ticks == 4, "a new tick zeroes the gauge and keeps the total");
}

static void test_observe_age_widens_tolerance(void) {
	slv_pos_part_stats ps;
	slv_pos_room_stats rs;
	memset(&ps, 0, sizeof(ps));
	memset(&rs, 0, sizeof(rs));
	slv_pos_sim sim = fresh(0, 0, 0, 800, 500, 1000000);
	double c[3] = { 2000, 0, 0 };   /* 20 m off */
	slv_pos_observe(&ps, &rs, &sim, 1000000, 1, 1, c);
	CHECK(ps.would_clamp == 1, "20 m off, fresh sample, tolerance 5 m: counted");
	slv_pos_observe(&ps, &rs, &sim, 3000000, 1, 1, c);
	CHECK(ps.would_clamp == 1, "the same claim 2 s later: tolerance 5 + 2 x 8 = 21 m, so not counted");
	CHECK(ps.age_ms == 2000, "the age is reported");
	slv_pos_observe(&ps, &rs, &sim, 2000000, 1, 1, c);
	CHECK(ps.would_clamp == 2, "1 s old: tolerance 13 m, counted");
}

static void test_observe_would_use_sim(void) {
	slv_pos_part_stats ps;
	slv_pos_room_stats rs;
	memset(&ps, 0, sizeof(ps));
	memset(&rs, 0, sizeof(rs));
	slv_pos_sim sim = fresh(0, 0, 0, 800, 500, 0);
	double none[3] = { 0, 0, 0 };
	slv_pos_room_tick_begin(&rs);
	slv_pos_observe(&ps, &rs, &sim, 1000, 0, 0, none);
	CHECK(ps.would_use_sim == 1 && rs.would_use_sim_ticks == 1 && rs.would_use_sim_now == 1, "no position at all: would use the sim's");
	CHECK(ps.would_clamp == 0 && ps.dev_cm == -1.0, "and there is no deviation to take");
	slv_pos_observe(&ps, &rs, &sim, 1000, 1, 0, none);
	CHECK(ps.would_use_sim == 2, "sp without lp: today's mix is flat, so the sim's would be used");
	slv_pos_observe(&ps, &rs, &sim, 1000, 0, 1, none);
	CHECK(ps.would_use_sim == 3, "lp without sp: counted");
	slv_pos_observe(&ps, &rs, &sim, 1000, 1, 1, none);
	CHECK(ps.would_use_sim == 3, "both present: not counted");
	slv_pos_observe(&ps, &rs, NULL, 1000, 0, 0, none);
	CHECK(ps.would_use_sim == 3 && ps.no_sim == 1, "with no sim point there is nothing to use: counted as no-sim instead");
}

static void test_observe_no_sim(void) {
	slv_pos_part_stats ps;
	slv_pos_room_stats rs;
	memset(&ps, 0, sizeof(ps));
	memset(&rs, 0, sizeof(rs));
	double c[3] = { 0, 0, 0 };
	slv_pos_room_tick_begin(&rs);
	slv_pos_observe(&ps, &rs, NULL, 5000000, 1, 1, c);
	CHECK(ps.state == SLV_POS_SIM_NONE && ps.no_sim == 1 && ps.no_sim_stale == 0 && ps.age_ms == -1, "missing: counted as no-sim");
	CHECK(rs.no_sim_ticks == 1 && rs.no_sim_now == 1 && rs.no_sim_stale_ticks == 0, "and in the room");
	slv_pos_sim old = fresh(0, 0, 0, 800, 500, 0);
	slv_pos_observe(&ps, &rs, &old, (int64_t)SLV_POS_STALE_MS * 1000, 1, 1, c);
	CHECK(ps.state == SLV_POS_SIM_FRESH && ps.no_sim == 1, "exactly at the stale age it is still fresh");
	slv_pos_observe(&ps, &rs, &old, (int64_t)SLV_POS_STALE_MS * 1000 + 1, 1, 1, c);
	CHECK(ps.state == SLV_POS_SIM_STALE && ps.no_sim == 2 && ps.no_sim_stale == 1, "one microsecond past it: stale, counted both ways");
	CHECK(rs.no_sim_ticks == 2 && rs.no_sim_stale_ticks == 1, "the room counts the stale one");
	double far[3] = { 1000000, 0, 0 };
	slv_pos_observe(&ps, &rs, &old, (int64_t)SLV_POS_STALE_MS * 1000 + 1, 1, 1, far);
	CHECK(ps.would_clamp == 0, "a stale point is not compared with the claim");
	CHECK(!strcmp(slv_pos_sim_state_str(SLV_POS_SIM_NONE), "none") && !strcmp(slv_pos_sim_state_str(SLV_POS_SIM_STALE), "stale")
		&& !strcmp(slv_pos_sim_state_str(SLV_POS_SIM_FRESH), "fresh"), "state names");
}

int main(void) {
	test_valid();
	test_envelope();
	test_entry_bounds();
	test_skip_and_count();
	test_size_caps();
	test_store();
	test_store_capacity();
	test_tolerance_and_age();
	test_observe_would_clamp();
	test_observe_age_widens_tolerance();
	test_observe_would_use_sim();
	test_observe_no_sim();
	printf("test_possync: %d checks, %d failures\n", g_checks, g_failures);
	return g_failures == 0 ? 0 : 1;
}
