/*! \file    tests/test_deferred.c
 * \author   Legion Voice Mixer project
 * \copyright GNU General Public License v3
 * \brief    Unit tests for the per-room deferred visibility/mute store (src/deferred.c).
 *
 * \details  Plain C, links NOTHING beyond libc (the module is Janus/glib/jansson-
 * free), like test_mix. Built and run by `make test`. Covers: defer-on-put and
 * get, the two channels being independent, REPLACE last-write-wins, ADD union,
 * REMOVE subtract, clear-on-empty (record removed), a REMOVE/empty on an absent
 * listener being a no-op, cap eviction of the OLDEST (with counter), replay_done
 * removing + counting, and free_all. Non-zero exit on failure.
 */

#include "../src/deferred.h"

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

static int col_has(char (*col)[SLV_UUID_LEN], int n, const char *s) {
	for(int i = 0; i < n; i++)
		if(!strcmp(col[i], s))
			return 1;
	return 0;
}

/* Deposit a variadic source list (up to 8) for one channel. */
static void put(slv_deferred_store *st, const char *L, slv_vis_op op, int is_mute,
		int n, const char *s0, const char *s1, const char *s2) {
	char col[8][SLV_UUID_LEN];
	const char *ss[3] = { s0, s1, s2 };
	for(int i = 0; i < n; i++) {
		col[i][0] = '\0';
		if(ss[i]) { strncpy(col[i], ss[i], SLV_UUID_LEN - 1); col[i][SLV_UUID_LEN - 1] = '\0'; }
	}
	slv_deferred_put(st, L, op, n > 0 ? col : NULL, n, is_mute);
}

static void test_defer_and_get(void) {
	slv_deferred_store st;
	slv_deferred_init(&st);
	put(&st, "L", SLV_VIS_OP_REPLACE, 0, 2, "S1", "S2", NULL);
	const slv_deferred_entry *d = slv_deferred_get(&st, "L");
	CHECK(d != NULL, "record created for absent listener");
	CHECK(d && d->n_excl == 2 && col_has(d->excl, d->n_excl, "S1") && col_has(d->excl, d->n_excl, "S2"),
		"excl column holds S1,S2");
	CHECK(d && d->n_mute == 0, "mute channel empty");
	CHECK(slv_deferred_count(&st) == 1, "one deferred record");
	CHECK(st.adds == 1, "adds counter is 1");
	CHECK(slv_deferred_get(&st, "other") == NULL, "unknown listener -> NULL");
	slv_deferred_free_all(&st);
}

static void test_channels_independent(void) {
	slv_deferred_store st;
	slv_deferred_init(&st);
	put(&st, "L", SLV_VIS_OP_ADD, 0, 1, "S1", NULL, NULL);   /* excl */
	put(&st, "L", SLV_VIS_OP_ADD, 1, 1, "S2", NULL, NULL);   /* mute */
	const slv_deferred_entry *d = slv_deferred_get(&st, "L");
	CHECK(d && d->n_excl == 1 && col_has(d->excl, 1, "S1"), "excl S1 kept");
	CHECK(d && d->n_mute == 1 && col_has(d->mute, 1, "S2"), "mute S2 kept independently");
	CHECK(slv_deferred_count(&st) == 1, "still one record (two channels)");
	slv_deferred_free_all(&st);
}

static void test_replace_last_write_wins(void) {
	slv_deferred_store st;
	slv_deferred_init(&st);
	put(&st, "L", SLV_VIS_OP_REPLACE, 0, 2, "S1", "S2", NULL);
	put(&st, "L", SLV_VIS_OP_REPLACE, 0, 1, "S3", NULL, NULL);
	const slv_deferred_entry *d = slv_deferred_get(&st, "L");
	CHECK(d && d->n_excl == 1 && col_has(d->excl, 1, "S3"), "REPLACE overwrites: only S3 remains");
	CHECK(st.replaced == 1, "replaced counter incremented");
	slv_deferred_free_all(&st);
}

static void test_add_union(void) {
	slv_deferred_store st;
	slv_deferred_init(&st);
	put(&st, "L", SLV_VIS_OP_ADD, 0, 1, "S1", NULL, NULL);
	put(&st, "L", SLV_VIS_OP_ADD, 0, 1, "S2", NULL, NULL);
	put(&st, "L", SLV_VIS_OP_ADD, 0, 1, "S1", NULL, NULL);   /* dup, no growth */
	const slv_deferred_entry *d = slv_deferred_get(&st, "L");
	CHECK(d && d->n_excl == 2 && col_has(d->excl, 2, "S1") && col_has(d->excl, 2, "S2"),
		"ADD unions to {S1,S2}, dup ignored");
	slv_deferred_free_all(&st);
}

static void test_remove_subtract(void) {
	slv_deferred_store st;
	slv_deferred_init(&st);
	put(&st, "L", SLV_VIS_OP_REPLACE, 0, 3, "S1", "S2", "S3");
	put(&st, "L", SLV_VIS_OP_REMOVE, 0, 1, "S2", NULL, NULL);
	const slv_deferred_entry *d = slv_deferred_get(&st, "L");
	CHECK(d && d->n_excl == 2 && col_has(d->excl, 2, "S1") && col_has(d->excl, 2, "S3") && !col_has(d->excl, 2, "S2"),
		"REMOVE subtracts S2 -> {S1,S3}");
	/* Remove the rest -> channel empties -> record dropped (both channels empty). */
	put(&st, "L", SLV_VIS_OP_REMOVE, 0, 2, "S1", "S3", NULL);
	CHECK(slv_deferred_get(&st, "L") == NULL, "emptied record removed");
	CHECK(slv_deferred_count(&st) == 0, "count back to 0");
	slv_deferred_free_all(&st);
}

static void test_clear_on_empty_replace(void) {
	slv_deferred_store st;
	slv_deferred_init(&st);
	put(&st, "L", SLV_VIS_OP_REPLACE, 0, 1, "S1", NULL, NULL);
	CHECK(slv_deferred_count(&st) == 1, "one record after first replace");
	put(&st, "L", SLV_VIS_OP_REPLACE, 0, 0, NULL, NULL, NULL);   /* empty replace clears excl */
	CHECK(slv_deferred_get(&st, "L") == NULL, "empty REPLACE clears the only channel -> record removed");
	CHECK(slv_deferred_count(&st) == 0, "count 0 after clear");
	slv_deferred_free_all(&st);
}

static void test_remove_absent_is_noop(void) {
	slv_deferred_store st;
	slv_deferred_init(&st);
	put(&st, "L", SLV_VIS_OP_REMOVE, 0, 1, "S1", NULL, NULL);   /* nothing deferred */
	CHECK(slv_deferred_count(&st) == 0, "REMOVE for absent listener creates nothing");
	CHECK(st.adds == 0, "no add counted");
	put(&st, "L", SLV_VIS_OP_REPLACE, 0, 0, NULL, NULL, NULL); /* empty replace, no prior record */
	CHECK(slv_deferred_count(&st) == 0, "empty REPLACE for absent listener is a no-op");
	slv_deferred_free_all(&st);
}

static void test_cap_eviction(void) {
	slv_deferred_store st;
	slv_deferred_init(&st);
	char name[24];
	for(int i = 0; i < SLV_VIS_MAX_DEFERRED; i++) {
		snprintf(name, sizeof(name), "L%d", i);
		put(&st, name, SLV_VIS_OP_ADD, 0, 1, "S", NULL, NULL);
	}
	CHECK(slv_deferred_count(&st) == SLV_VIS_MAX_DEFERRED, "store filled to cap");
	CHECK(st.adds == (uint64_t)SLV_VIS_MAX_DEFERRED, "adds == cap");
	CHECK(st.evicted == 0, "nothing evicted yet");
	/* One past the cap: the OLDEST (L0) is evicted. */
	put(&st, "LNEW", SLV_VIS_OP_ADD, 0, 1, "S", NULL, NULL);
	CHECK(slv_deferred_count(&st) == SLV_VIS_MAX_DEFERRED, "count stays at cap");
	CHECK(st.evicted == 1, "one eviction counted");
	CHECK(slv_deferred_get(&st, "L0") == NULL, "oldest (L0) evicted");
	CHECK(slv_deferred_get(&st, "L1") != NULL, "second-oldest (L1) survives");
	CHECK(slv_deferred_get(&st, "LNEW") != NULL, "newest present");
	slv_deferred_free_all(&st);
}

static void test_replay_done(void) {
	slv_deferred_store st;
	slv_deferred_init(&st);
	put(&st, "L", SLV_VIS_OP_REPLACE, 0, 1, "S1", NULL, NULL);
	CHECK(slv_deferred_replay_done(&st, "L") == 1, "replay_done removes the record");
	CHECK(slv_deferred_count(&st) == 0, "count 0 after replay");
	CHECK(st.replayed == 1, "replayed counter incremented");
	CHECK(slv_deferred_replay_done(&st, "L") == 0, "replay_done on absent listener returns 0");
	slv_deferred_free_all(&st);
}

static void test_free_all(void) {
	slv_deferred_store st;
	slv_deferred_init(&st);
	put(&st, "A", SLV_VIS_OP_REPLACE, 0, 2, "S1", "S2", NULL);
	put(&st, "B", SLV_VIS_OP_REPLACE, 1, 1, "S3", NULL, NULL);
	slv_deferred_free_all(&st);
	CHECK(slv_deferred_count(&st) == 0, "free_all empties the store");
	/* Store is reusable after free_all. */
	put(&st, "C", SLV_VIS_OP_ADD, 0, 1, "S4", NULL, NULL);
	CHECK(slv_deferred_count(&st) == 1, "store usable again after free_all");
	slv_deferred_free_all(&st);
}

int main(void) {
	test_defer_and_get();
	test_channels_independent();
	test_replace_last_write_wins();
	test_add_union();
	test_remove_subtract();
	test_clear_on_empty_replace();
	test_remove_absent_is_noop();
	test_cap_eviction();
	test_replay_done();
	test_free_all();

	printf("test_deferred: %d checks, %d failures\n", g_checks, g_failures);
	return g_failures == 0 ? 0 : 1;
}
