/*! \file    deferred.h
 * \author   Legion Voice Mixer project
 * \copyright GNU General Public License v3
 * \brief    Per-room store of visibility/mute columns DEFERRED for a listener
 *           that is not yet in the Janus room (join-window fix).
 *
 * \details  janus_slvoice_apply_visbatch / apply_mutebatch drop a batch entry
 * whose listener has not joined the room yet (nmatch==0). Dropping it loses an
 * exclusion/mute that arrived between provision and room-join until the next
 * snapshot. This store retains that listener's LATEST intended columns so the
 * join branch can REPLAY them the instant the listener joins, before any
 * presence derived from them (the join roster, push_presence, the data_ready
 * backlog) is revealed.
 *
 * Like src/visbatch.c and src/sldata.c this module is DEPENDENCY-FREE beyond
 * libc (no Janus, no glib, no jansson), so it compiles both into the plugin .so
 * and into a standalone unit test (tests/test_deferred.c, `make test`).
 *
 * Semantics (per channel, last write for a listener wins):
 *   - REPLACE : set the channel to the entry's sources EXACTLY (empty => clear).
 *   - ADD     : union the entry's sources into the channel.
 *   - REMOVE  : subtract the entry's sources from the channel (empty => clear).
 * A record whose BOTH channels become empty is removed (nothing to replay).
 * Bounded by ::SLV_VIS_MAX_DEFERRED per room; on overflow the OLDEST record
 * (lowest added_seq) is evicted and counted. A listener that never arrives is
 * bounded by that cap plus room teardown — there is deliberately NO TTL (a
 * proven join took +8min; a timer would just recreate the loss it fixes).
 */

#ifndef SLV_DEFERRED_H
#define SLV_DEFERRED_H

#include <stdint.h>
#include "visbatch.h"   /* SLV_UUID_LEN, slv_vis_op, SLV_VIS_MAX_DEFERRED */

/*! \brief One deferred listener's latest intended columns. */
typedef struct slv_deferred_entry {
	char listener[SLV_UUID_LEN];    /*!< listener (agent UUID) key */
	char (*excl)[SLV_UUID_LEN];     /*!< latest deferred EXCLUSION column (NULL if none) */
	int n_excl;
	char (*mute)[SLV_UUID_LEN];     /*!< latest deferred MODERATION-MUTE column (NULL if none) */
	int n_mute;
	uint64_t added_seq;             /*!< store->seq stamp at first insert (oldest-eviction key) */
} slv_deferred_entry;

/*! \brief Fixed-capacity per-room deferred store. Zero-initialised = empty. */
typedef struct slv_deferred_store {
	slv_deferred_entry entries[SLV_VIS_MAX_DEFERRED];
	int n;               /*!< current record count (== deferred_current) */
	uint64_t seq;        /*!< monotonic stamp source for added_seq */
	/* Cumulative counters (since store init; mirror the room vis_* counters). */
	uint64_t adds;       /*!< records created */
	uint64_t replaced;   /*!< existing records updated by a newer batch entry */
	uint64_t replayed;   /*!< records replayed (taken) on a listener join */
	uint64_t evicted;    /*!< records evicted on cap overflow */
} slv_deferred_store;

/*! \brief Zero a store to the empty, ready state. */
void slv_deferred_init(slv_deferred_store *st);

/*! \brief Free every record's heap columns and reset the count to 0. Cumulative
 * counters are retained (like the room's cumulative vis_* counters). Safe on a
 * zeroed store; idempotent. */
void slv_deferred_free_all(slv_deferred_store *st);

/*! \brief Deposit one dropped channel entry for \c listener. \c is_mute selects
 * the MUTE channel (else the EXCLUSION channel). \c src / \c n_src are the
 * entry's source column (the slv_vis_entry excl[] array — the mute channel reuses
 * the same field). Applies the op semantics documented above; may create, update,
 * evict, or clear a record, bumping the matching counter. No-op for a REMOVE or an
 * empty column naming a listener with no existing record. */
void slv_deferred_put(slv_deferred_store *st, const char *listener, slv_vis_op op,
	char (*src)[SLV_UUID_LEN], int n_src, int is_mute);

/*! \brief Look up a listener's record, or NULL. The pointer is valid until the
 * next mutating call for that listener (put / replay_done / free_all). */
const slv_deferred_entry *slv_deferred_get(const slv_deferred_store *st, const char *listener);

/*! \brief Remove a listener's record after the caller has replayed it, bumping
 * the replayed counter. Returns 1 if a record was removed, 0 if none existed. */
int slv_deferred_replay_done(slv_deferred_store *st, const char *listener);

/*! \brief Current number of deferred listeners (deferred_current). */
int slv_deferred_count(const slv_deferred_store *st);

#endif /* SLV_DEFERRED_H */
