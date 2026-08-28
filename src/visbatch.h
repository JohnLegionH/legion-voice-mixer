/*! \file    visbatch.h
 * \author   Legion Voice Mixer project
 * \copyright GNU General Public License v3
 * \brief    Parser for the server-to-server per-listener visibility batch
 *           ("peer_ctl_batch" / slvoice_vis, docs/voice/mixer-feed-protocol.md §3.3)
 *
 * \details  The C# VoiceStateFeeder pushes per-listener exclusion updates to the
 * mixer over the server-to-server admin surface (NOT the viewer SLData channel).
 * This module parses one such batch into a flat, typed \ref slv_visbatch.
 *
 * Like src/sldata.c it is DEPENDENCY-FREE beyond jansson + libc (no Janus
 * headers), so it compiles both into the plugin .so and into a standalone unit
 * test (tests/test_visbatch.c, `make test`). The buf/len interface hides jansson
 * from callers, matching slv_sldata_parse.
 *
 * Wire shape (spec §3.3), accepted either at top level or wrapped in "slvoice_vis":
 *   { "op":"add|remove|replace", "room":<int>, "excl": { "<L>":["<S>",...], ... } }
 *
 * Resilience posture (matches the feeder's tick hardening): a batch that lacks a
 * valid op/room is rejected whole (SLV_VISBATCH_MALFORMED); a malformed
 * per-listener or per-source item is SKIPPED and counted in \c n_skipped — the
 * rest of the batch still parses.
 */

#ifndef SLV_VISBATCH_H
#define SLV_VISBATCH_H

#include <stddef.h>
#include <stdint.h>

/* Shared with sldata.h; guarded so both may be included in the plugin TU. */
#ifndef SLV_UUID_LEN
#define SLV_UUID_LEN 40
#endif

/*! \brief Hard cap on one batch payload; larger is rejected without parsing. A
 * snapshot-replace for one listener at the doc's scale is a few KB; a fan-out
 * delta touches many listeners but few sources each. */
#define SLV_VISBATCH_MAX_BYTES 65536

/*! \brief Max listener entries parsed from one batch (extras skipped+counted). */
#define SLV_VIS_MAX_ENTRIES 128

/*! \brief Max excluded sources parsed per listener (extras skipped+counted). */
#define SLV_VIS_MAX_EXCL 128

/*! \brief Max DEFERRED (provisioned-but-not-yet-joined) listeners retained per
 * room by the deferred store (src/deferred.h). A fixed cap in the style of
 * ::SLV_VIS_MAX_ENTRIES: on overflow the OLDEST deferred record is evicted and
 * counted. A listener that never arrives is bounded by this cap plus room
 * teardown — deliberately no TTL (a proven join took +8min). */
#define SLV_VIS_MAX_DEFERRED 256

/*! \brief Which merge the batch performs into each listener's exclusion set. */
typedef enum slv_vis_op {
	SLV_VIS_OP_ADD = 0,    /*!< add these (listener,source) exclusions */
	SLV_VIS_OP_REMOVE,     /*!< remove these exclusions */
	SLV_VIS_OP_REPLACE,    /*!< set the listener's set EXACTLY (snapshot sub-mode) */
} slv_vis_op;

/*! \brief One listener's exclusion update: the listener UUID plus the source
 * UUIDs. For \c SLV_VIS_OP_REPLACE an empty \c excl (n_excl==0) is meaningful —
 * it clears the listener's set. */
typedef struct slv_vis_entry {
	char listener[SLV_UUID_LEN];       /*!< listener (agent UUID) */
	char (*excl)[SLV_UUID_LEN];        /*!< heap array of \c n_excl source UUIDs */
	int n_excl;                        /*!< number of source UUIDs */
} slv_vis_entry;

/*! \brief A parsed visibility batch. Heap-owned; free with slv_visbatch_free. */
typedef struct slv_visbatch {
	slv_vis_op op;
	int64_t room;                      /*!< target room number (matches the mixer room_id) */
	slv_vis_entry *entries;            /*!< heap array of \c n_entries EXCLUSION (ban/visibility) updates */
	int n_entries;
	/* ADDITIVE moderation MUTE channel (Option A). Same slv_vis_entry shape as `entries`, but a
	 * source here is MUTED (row stays, greyed) rather than excluded (removed). Parsed from the
	 * optional top-level "mute" object; an absent "mute" leaves this NULL/0 — today's behaviour
	 * exactly. An older mixer that never reads this field is unaffected (skew-safe). */
	slv_vis_entry *mute_entries;       /*!< heap array of \c n_mute_entries moderation-mute updates */
	int n_mute_entries;
	int n_skipped;                     /*!< malformed listener/source items skipped (both channels) */
} slv_visbatch;

/*! \brief Result of a parse attempt. */
typedef enum slv_visbatch_status {
	SLV_VISBATCH_OK = 0,     /*!< valid; >=1 applicable listener entry */
	SLV_VISBATCH_EMPTY,      /*!< valid op/room but no applicable entries */
	SLV_VISBATCH_MALFORMED,  /*!< not a JSON object, or missing/invalid op or room */
	SLV_VISBATCH_TOOBIG,     /*!< payload exceeded ::SLV_VISBATCH_MAX_BYTES */
} slv_visbatch_status;

/*! \brief Parse one raw JSON batch payload (NOT required to be NUL-terminated).
 * \c out is fully zero-initialised first. On OK/EMPTY the caller must call
 * slv_visbatch_free(out). Never dereferences past \c len; never aborts. */
slv_visbatch_status slv_visbatch_parse(const char *buf, size_t len, slv_visbatch *out);

/*! \brief Free heap owned by a parsed batch. Safe on a zeroed struct. */
void slv_visbatch_free(slv_visbatch *b);

/*! \brief Stable op name for logs/diagnostics ("add"/"remove"/"replace"). */
const char *slv_vis_op_str(slv_vis_op op);

#endif /* SLV_VISBATCH_H */
