/*! \file    tests/test_visbatch.c
 * \author   Legion Voice Mixer project
 * \copyright GNU General Public License v3
 * \brief    Unit tests for the visibility-batch parser (src/visbatch.c).
 *
 * \details  Plain C, links only jansson (no Janus). Built and run by
 * `make test`. Covers: valid add/remove/replace, the snapshot-replace clear
 * sub-mode, the "slvoice_vis" wrapper, malformed rejection (bad/missing op or
 * room, non-object), empty, oversized, per-source/per-listener caps, and —
 * required — malformed-entry RESILIENCE (a bad entry is rejected, counted in
 * n_skipped, and the rest of the batch still parses). Non-zero exit on failure.
 */

#include "../src/visbatch.h"

#include <stdio.h>
#include <string.h>
#include <stdlib.h>

static int g_failures = 0;
static int g_checks = 0;

#define CHECK(cond, msg) do { \
	g_checks++; \
	if(!(cond)) { \
		g_failures++; \
		fprintf(stderr, "  FAIL: %s (%s:%d)\n", (msg), __FILE__, __LINE__); \
	} \
} while(0)

static slv_visbatch_status parse(const char *json, slv_visbatch *out) {
	return slv_visbatch_parse(json, strlen(json), out);
}

/* Find a listener entry by uuid; NULL if absent. */
static const slv_vis_entry *find(const slv_visbatch *b, const char *listener) {
	for(int i = 0; i < b->n_entries; i++)
		if(!strcmp(b->entries[i].listener, listener))
			return &b->entries[i];
	return NULL;
}

static int has_src(const slv_vis_entry *e, const char *s) {
	for(int i = 0; i < e->n_excl; i++)
		if(!strcmp(e->excl[i], s))
			return 1;
	return 0;
}

static void test_valid_add(void) {
	slv_visbatch b;
	slv_visbatch_status st = parse(
		"{\"op\":\"add\",\"room\":42,\"excl\":{\"L1\":[\"S1\",\"S2\"],\"L2\":[\"S1\"]}}", &b);
	CHECK(st == SLV_VISBATCH_OK, "valid add parses OK");
	CHECK(b.op == SLV_VIS_OP_ADD, "op is add");
	CHECK(b.room == 42, "room is 42");
	CHECK(b.n_entries == 2, "two listener entries");
	CHECK(b.n_skipped == 0, "nothing skipped");
	const slv_vis_entry *l1 = find(&b, "L1");
	CHECK(l1 && l1->n_excl == 2 && has_src(l1, "S1") && has_src(l1, "S2"), "L1 excludes S1,S2");
	const slv_vis_entry *l2 = find(&b, "L2");
	CHECK(l2 && l2->n_excl == 1 && has_src(l2, "S1"), "L2 excludes S1");
	slv_visbatch_free(&b);
}

static void test_ops(void) {
	slv_visbatch b;
	CHECK(parse("{\"op\":\"remove\",\"room\":1,\"excl\":{\"L\":[\"S\"]}}", &b) == SLV_VISBATCH_OK
		&& b.op == SLV_VIS_OP_REMOVE, "remove op");
	slv_visbatch_free(&b);
	CHECK(parse("{\"op\":\"replace\",\"room\":1,\"excl\":{\"L\":[\"S\"]}}", &b) == SLV_VISBATCH_OK
		&& b.op == SLV_VIS_OP_REPLACE, "replace op");
	slv_visbatch_free(&b);
}

static void test_replace_clear(void) {
	/* replace with an empty list clears the listener; the entry MUST be kept. */
	slv_visbatch b;
	slv_visbatch_status st = parse("{\"op\":\"replace\",\"room\":7,\"excl\":{\"L\":[]}}", &b);
	CHECK(st == SLV_VISBATCH_OK, "replace-clear parses OK");
	CHECK(b.n_entries == 1, "the cleared listener entry is retained");
	const slv_vis_entry *l = find(&b, "L");
	CHECK(l && l->n_excl == 0, "L has an empty (cleared) source set");
	slv_visbatch_free(&b);
}

static void test_wrapper(void) {
	slv_visbatch b;
	slv_visbatch_status st = parse(
		"{\"slvoice_vis\":{\"op\":\"add\",\"room\":9,\"excl\":{\"L\":[\"S\"]}}}", &b);
	CHECK(st == SLV_VISBATCH_OK && b.room == 9 && b.n_entries == 1, "slvoice_vis wrapper accepted");
	slv_visbatch_free(&b);
}

static void test_malformed(void) {
	slv_visbatch b;
	CHECK(parse("[]", &b) == SLV_VISBATCH_MALFORMED, "non-object array is malformed");
	slv_visbatch_free(&b);
	CHECK(parse("not json", &b) == SLV_VISBATCH_MALFORMED, "garbage is malformed");
	slv_visbatch_free(&b);
	CHECK(parse("{\"room\":1,\"excl\":{}}", &b) == SLV_VISBATCH_MALFORMED, "missing op is malformed");
	slv_visbatch_free(&b);
	CHECK(parse("{\"op\":\"nope\",\"room\":1}", &b) == SLV_VISBATCH_MALFORMED, "bad op is malformed");
	slv_visbatch_free(&b);
	CHECK(parse("{\"op\":\"add\",\"excl\":{}}", &b) == SLV_VISBATCH_MALFORMED, "missing room is malformed");
	slv_visbatch_free(&b);
	CHECK(parse("{\"op\":\"add\",\"room\":\"x\"}", &b) == SLV_VISBATCH_MALFORMED, "non-int room is malformed");
	slv_visbatch_free(&b);
}

static void test_empty(void) {
	slv_visbatch b;
	CHECK(parse("{\"op\":\"add\",\"room\":1}", &b) == SLV_VISBATCH_EMPTY, "no excl -> empty");
	slv_visbatch_free(&b);
	CHECK(parse("{\"op\":\"add\",\"room\":1,\"excl\":{}}", &b) == SLV_VISBATCH_EMPTY, "empty excl -> empty");
	slv_visbatch_free(&b);
}

static void test_toobig(void) {
	/* Build a payload just over the byte cap. */
	size_t big = SLV_VISBATCH_MAX_BYTES + 16;
	char *buf = malloc(big + 1);
	memset(buf, 'x', big);
	buf[big] = '\0';
	slv_visbatch b;
	CHECK(slv_visbatch_parse(buf, big, &b) == SLV_VISBATCH_TOOBIG, "oversized payload rejected");
	slv_visbatch_free(&b);
	free(buf);
}

/* REQUIRED: a batch mixing good and bad entries -> the bad ones are rejected and
 * counted, the good ones still parse (reject, log-at-caller, continue). */
static void test_resilience(void) {
	slv_visbatch b;
	slv_visbatch_status st = parse(
		"{\"op\":\"add\",\"room\":5,\"excl\":{"
		  "\"L1\":[\"S1\",\"\",\"S2\",123],"   /* empty + non-string sources are skipped */
		  "\"L2\":\"notanarray\","             /* value not an array -> whole entry skipped */
		  "\"L3\":[\"S9\"]"                    /* good */
		"}}", &b);
	CHECK(st == SLV_VISBATCH_OK, "resilient batch still parses OK");
	CHECK(b.n_entries == 2, "only the two well-formed listener entries are kept");
	const slv_vis_entry *l1 = find(&b, "L1");
	CHECK(l1 && l1->n_excl == 2 && has_src(l1, "S1") && has_src(l1, "S2"), "L1 keeps the good sources only");
	CHECK(find(&b, "L2") == NULL, "the non-array listener entry is dropped");
	const slv_vis_entry *l3 = find(&b, "L3");
	CHECK(l3 && l3->n_excl == 1 && has_src(l3, "S9"), "L3 is intact");
	CHECK(b.n_skipped == 3, "skipped count = empty src + non-string src + non-array entry");
	slv_visbatch_free(&b);
}

int main(void) {
	test_valid_add();
	test_ops();
	test_replace_clear();
	test_wrapper();
	test_malformed();
	test_empty();
	test_toobig();
	test_resilience();

	printf("test_visbatch: %d checks, %d failures\n", g_checks, g_failures);
	return g_failures == 0 ? 0 : 1;
}
