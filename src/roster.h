/*! \file    roster.h
 * \author   Legion Voice Mixer project
 * \copyright GNU General Public License v3
 * \brief    Single-source-of-truth per-listener visibility predicate.
 *
 * \details  Every mixer->client emission site consults ONE function to decide
 * whether a listener may hear/see a source: the audio cull in the mix loop, the
 * per-listener power/VAD roster batch, the live join/leave presence notice, AND
 * the initial roster (join-backlog) a newly-connecting listener receives. Keeping
 * the decision in one place is exactly the single-source-of-truth rule that keeps
 * dots and audio from drifting (mixer-feed-protocol.md §1).
 *
 * Header-only + glib-only (no Janus), so it compiles into the plugin and into a
 * standalone unit test (tests/test_roster.c, `make test`).
 */

#ifndef SLV_ROSTER_H
#define SLV_ROSTER_H

#include <glib.h>

/*! \brief TRUE if a listener whose exclusion set is \p excluded must NOT hear or
 * see the source \p uuid. A NULL set (or NULL uuid) excludes nothing — the
 * listener sees all. The set holds TARGET (source) agent-UUID strings, populated
 * from the server-to-server peer_ctl_batch feed. */
static inline gboolean slv_roster_excludes(GHashTable *excluded, const char *uuid) {
	return (excluded != NULL && uuid != NULL && g_hash_table_contains(excluded, uuid));
}

#endif /* SLV_ROSTER_H */
