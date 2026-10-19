/* Copyright (c) 2026 Legion Builds
 * SPDX-License-Identifier: GPL-3.0-only
 * Licensed under the GNU General Public License, version 3 only; see the LICENSE file in the repository root.
 */
/*! \file    possync.h
 * \author   Legion Voice Mixer project
 * \copyright GNU General Public License v3
 * \brief    Sim-reported participant positions ("peer_pos", vis_protocol 3): the parser, the per-room store
 *           and the observe-only comparison with the position a viewer claims for itself.
 *
 * \details  A simulator that knows where each avatar really is can tell the mixer, so the mixer can compare
 * that with the "sp" a viewer reports in its SLData. This revision only OBSERVES: it counts what correcting a
 * viewer's claim would do and changes nothing in the mix.
 *
 * Wire shape (admin API, same envelope as peer_ctl_batch), accepted at top level:
 *   { "request":"peer_pos", "room":<int>, "seq":<int>, "full":<bool>, "mode":"observe|enforce",
 *     "room_epoch":"<16 hex>",
 *     "pos": { "<agent uuid>": [x_cm, y_cm, z_cm, speed_cm_s, tolerance_cm], ... } }
 *
 *  - x, y, z are global centimetres, the frame a viewer reports "sp" in (its position times 100).
 *  - speed_cm_s is how many centimetres per second further from the point the participant may be for each
 *    second since the mixer received it; tolerance_cm is the difference to accept at the instant of the sample.
 *  - full true: the entries are the whole room, a participant not listed has left. full false: only the listed
 *    participants change.
 *  - seq only rises per room; a request whose seq is not above the last accepted is ignored.
 *
 * Like visbatch.c the parser is DEPENDENCY-FREE beyond jansson + libc (no Janus or glib), so it compiles into the
 * plugin and into tests/test_possync.c. The store is a fixed open-addressing table with no allocation.
 *
 * Resilience posture (as visbatch): a request without a valid room, seq, full or pos object is rejected whole
 * (SLV_POS_MALFORMED); a request over SLV_POS_MAX_BYTES bytes or SLV_POS_MAX_ENTRIES entries is rejected whole
 * (SLV_POS_TOOBIG); a malformed single entry is SKIPPED and counted in n_skipped, and the rest still applies.
 */

#ifndef SLV_POSSYNC_H
#define SLV_POSSYNC_H

#include <stddef.h>
#include <stdint.h>

#ifndef SLV_UUID_LEN
#define SLV_UUID_LEN 40
#endif

/*! \brief Largest accepted request body; anything bigger is rejected without parsing. */
#define SLV_POS_MAX_BYTES 65536
/*! \brief Most entries one request may carry (the same bound as a visibility batch). A request with more is
 * rejected whole, the way the sim's 120-entry chunking assumes. */
#define SLV_POS_MAX_ENTRIES 128
/*! \brief Largest |coordinate| accepted, in centimetres: the range of a 32-bit integer, which is what the sim
 * writes and what the viewer's own "sp" is. */
#define SLV_POS_COORD_MAX 2147483647LL
/*! \brief Largest speed allowance accepted, cm/s (2 km/s). A larger one is a malformed entry, not a clamp. */
#define SLV_POS_SPEED_MAX_CMS 200000LL
/*! \brief Largest tolerance accepted, cm (5 km). */
#define SLV_POS_TOL_MAX_CM 500000LL
/*! \brief Entries a room's store holds. Above SLV_MAX_MIX so a participant who has not joined yet still has a
 * slot; a full snapshot replaces the whole store, so it never grows past the sim's room. */
#define SLV_POS_STORE_MAX 256
/*! \brief Slots in the open-addressing table (a power of two, twice the store maximum). */
#define SLV_POS_SLOTS 512
/*! \brief A sim point older than this is stale (the mixer's own receive clock). Four of the sim's one-second
 * keep-alive snapshots; also the 6000 ms the position design names. */
#define SLV_POS_STALE_MS 6000

typedef enum slv_pos_status {
	SLV_POS_OK = 0,      /*!< valid; the entries (possibly none) apply */
	SLV_POS_MALFORMED,   /*!< not an object, or a missing/invalid room, seq, full, pos or room_epoch */
	SLV_POS_TOOBIG       /*!< over SLV_POS_MAX_BYTES bytes or SLV_POS_MAX_ENTRIES entries */
} slv_pos_status;

/*! \brief One participant as the sim reports it. */
typedef struct slv_pos_entry {
	char uuid[SLV_UUID_LEN];   /*!< canonical lowercase agent UUID */
	int64_t x, y, z;           /*!< global centimetres */
	int64_t speed_cms;         /*!< cm/s the claim may move away per second of age */
	int64_t tol_cm;            /*!< cm of difference accepted at the instant of the sample */
} slv_pos_entry;

/*! \brief A parsed peer_pos request. Heap-owned; free with slv_posbatch_free. */
typedef struct slv_posbatch {
	int64_t room;
	int64_t seq;
	int full;
	int enforce_requested;     /*!< "mode" was "enforce" (the sim asking; this mixer only observes) */
	int has_epoch;
	uint64_t room_epoch;
	slv_pos_entry *entries;
	int n_entries;
	int n_skipped;             /*!< entries that were malformed or out of range */
} slv_posbatch;

/*! \brief Parse one raw peer_pos body (not required to be NUL-terminated). \c out is zeroed first. Never
 * dereferences past \c len. On SLV_POS_OK the caller must call slv_posbatch_free. */
slv_pos_status slv_posbatch_parse(const char *buf, size_t len, slv_posbatch *out);
void slv_posbatch_free(slv_posbatch *b);
const char *slv_pos_status_str(slv_pos_status st);

/*! \brief A stored sim point: the entry plus when the mixer received it. */
typedef struct slv_pos_sim {
	int used;
	slv_pos_entry e;
	int64_t recv_us;           /*!< the mixer's own monotonic clock at receipt */
} slv_pos_sim;

/*! \brief The per-room store of sim points, keyed by lowercase agent UUID. */
typedef struct slv_pos_store {
	slv_pos_sim slot[SLV_POS_SLOTS];
	int count;
} slv_pos_store;

void slv_pos_store_clear(slv_pos_store *st);

/*! \brief Apply a parsed request: a full request first empties the store. Entries beyond SLV_POS_STORE_MAX
 * new participants are dropped and counted in \c *dropped_full. Returns the number of entries stored or updated. */
int slv_pos_store_apply(slv_pos_store *st, const slv_posbatch *b, int64_t now_us, int *dropped_full);

/*! \brief The stored point for a UUID (any case, up to SLV_UUID_LEN - 1 characters), or NULL. */
const slv_pos_sim *slv_pos_store_find(const slv_pos_store *st, const char *uuid);

/*! \brief The tolerance in force \c age_us after the sample: tol + speed x age, in centimetres. */
double slv_pos_tolerance_now(const slv_pos_sim *sim, int64_t age_us);

typedef enum slv_pos_sim_state {
	SLV_POS_SIM_NONE = 0,   /*!< no sim point for this participant */
	SLV_POS_SIM_STALE,      /*!< a sim point older than SLV_POS_STALE_MS */
	SLV_POS_SIM_FRESH
} slv_pos_sim_state;

const char *slv_pos_sim_state_str(slv_pos_sim_state s);

/*! \brief What observing one participant for one tick counted, per participant (guarded by the session mutex). */
typedef struct slv_pos_part_stats {
	slv_pos_sim_state state;        /*!< at the last tick */
	int64_t age_ms;                 /*!< age of the sim point at the last tick; -1 with none */
	slv_pos_entry sim;              /*!< the sim point at the last tick (valid unless state is NONE) */
	double dev_cm;                  /*!< |claim - sim| at the last tick; -1 when it could not be taken */
	uint64_t ticks_observed;
	uint64_t would_clamp;           /*!< ticks the claim was beyond the tolerance of the sim point */
	double would_clamp_max_dev_cm;  /*!< the largest |claim - sim| over those ticks */
	double would_clamp_max_excess_cm; /*!< the largest amount beyond the tolerance over those ticks */
	uint64_t would_use_sim;         /*!< ticks the viewer had no usable position and a fresh sim point existed */
	uint64_t no_sim;                /*!< ticks with no fresh sim point while the room was reporting positions */
	uint64_t no_sim_stale;          /*!< the subset of no_sim where a point existed but was stale */
} slv_pos_part_stats;

/*! \brief The same, per room: totals over every participant and tick, and the request counters. */
typedef struct slv_pos_room_stats {
	int have_batch;                 /*!< a request has been accepted: observing starts, not before */
	int64_t last_seq;
	int64_t last_us;                /*!< mixer clock at the last accepted request */
	uint64_t epoch;                 /*!< the room_epoch of the last accepted request, 0 if it carried none */
	uint64_t batches;
	uint64_t full_batches;
	uint64_t entries_applied;
	uint64_t entries_skipped;
	uint64_t entries_dropped_full;
	uint64_t rejected_stale_epoch;
	uint64_t rejected_stale_seq;
	uint64_t enforce_requests;      /*!< accepted requests whose mode was "enforce" */
	uint64_t would_clamp_ticks;     /*!< participant-ticks, as in slv_pos_part_stats */
	uint64_t would_use_sim_ticks;
	uint64_t no_sim_ticks;
	uint64_t no_sim_stale_ticks;
	double would_clamp_max_dev_cm;
	double would_clamp_max_excess_cm;
	uint32_t would_clamp_now;       /*!< participants over the tolerance at the last tick */
	uint32_t would_use_sim_now;
	uint32_t no_sim_now;
} slv_pos_room_stats;

/*! \brief Zero the per-tick gauges; call once before observing a tick's participants. */
void slv_pos_room_tick_begin(slv_pos_room_stats *rs);

/*! \brief Observe one participant for one tick and count; changes nothing else.
 * \param sim the stored sim point or NULL
 * \param has_sp the viewer's last SLData carried "sp" (claim_sp valid), has_lp the same for "lp"
 * \param claim_sp the claim in centimetres (x, y, z) */
void slv_pos_observe(slv_pos_part_stats *ps, slv_pos_room_stats *rs, const slv_pos_sim *sim, int64_t now_us,
		int has_sp, int has_lp, const double claim_sp[3]);

#endif /* SLV_POSSYNC_H */
