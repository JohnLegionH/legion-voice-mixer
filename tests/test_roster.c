/*! \file    tests/test_roster.c
 * \author   Legion Voice Mixer project
 * \copyright GNU General Public License v3
 * \brief    Unit tests for the single-source-of-truth exclusion predicate
 *           (src/roster.h) that gates every mixer->client emission site.
 *
 * \details  Plain C, links only glib (no Janus). Built and run by `make test`.
 * Encodes the initial-roster / join-backlog rule the plugin now applies when a
 * newly-connecting listener receives the list of already-present speakers: a
 * speaker the listener excludes is omitted (no join emitted toward them), while
 * non-excluded speakers are emitted normally. The SAME predicate gates the live
 * join/leave notice, the power/VAD batch, and the mix cull.
 */

#include "../src/roster.h"

#include <glib.h>
#include <stdio.h>

static int g_failures = 0;
static int g_checks = 0;

#define CHECK(cond, msg) do { \
	g_checks++; \
	if(!(cond)) { \
		g_failures++; \
		fprintf(stderr, "  FAIL: %s\n", (msg)); \
	} \
} while(0)

int main(void) {
	/* A listener L that already excludes speaker "S_ex" (e.g. a reconnect where the
	 * feeder pushed L's snapshot before L's roster was built). "S_ok" is a speaker
	 * L is allowed to hear. */
	GHashTable *excluded = g_hash_table_new_full(g_str_hash, g_str_equal, g_free, NULL);
	g_hash_table_add(excluded, g_strdup("S_ex"));

	/* Initial roster / join-backlog toward L: the excluded speaker is omitted
	 * (no join emitted toward L); a non-excluded speaker is emitted normally. */
	CHECK(slv_roster_excludes(excluded, "S_ex") == TRUE,
		"an already-present excluded speaker is omitted from a new listener's initial roster");
	CHECK(slv_roster_excludes(excluded, "S_ok") == FALSE,
		"a non-excluded speaker is emitted to the new listener normally");

	/* A freshly-connected listener whose feed has not arrived yet (NULL or empty
	 * set) sees everyone — fail-open, then corrected by the next batch. */
	CHECK(slv_roster_excludes(NULL, "S_ex") == FALSE, "NULL exclusion set excludes nothing");
	GHashTable *empty = g_hash_table_new(g_str_hash, g_str_equal);
	CHECK(slv_roster_excludes(empty, "S_ex") == FALSE, "empty exclusion set excludes nothing");

	/* Defensive: a NULL candidate must not crash or falsely exclude. */
	CHECK(slv_roster_excludes(excluded, NULL) == FALSE, "NULL source uuid excludes nothing");

	g_hash_table_destroy(excluded);
	g_hash_table_destroy(empty);

	printf("test_roster: %d checks, %d failures\n", g_checks, g_failures);
	return g_failures == 0 ? 0 : 1;
}
