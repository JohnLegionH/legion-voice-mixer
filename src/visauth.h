/*! \file    visauth.h
 * \author   Legion Voice Mixer project
 * \copyright GNU General Public License v3
 * \brief    Phase 0 visibility authority: epoch parsing, the epoch adoption rule, the listener rule rows,
 *           and the two knobs (docs/voice/nonspatial-phase0-design.md §1, §4, §5, §6.1).
 *
 * \details  Header-only and libc-only, like roster.h, so both the plugin and the dependency-free
 * visbatch.c parser use it, and tests/test_visauth.c exercises it directly. It holds the pure rules;
 * the plugin holds the state (per room: the authority epoch, the policy generation, and one listener
 * record per display) under room->mutex.
 */

#ifndef SLV_VISAUTH_H
#define SLV_VISAUTH_H

#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

/*! \brief The wire protocol a Phase 0 mixer advertises in every peer_ctl_batch and heartbeat reply (§3). */
#define SLV_VIS_PROTOCOL 2

/*! \brief JS_VIS_STALE_MS default (§5). */
#define SLV_VIS_STALE_MS_DEFAULT 8000
/*! \brief Largest accepted JS_VIS_STALE_MS; anything above is ignored with a WARN. */
#define SLV_VIS_STALE_MS_MAX 600000
/*! \brief The sim's heartbeat interval (§3, VisAuthority.HeartbeatIntervalMs), used for the startup clamp. */
#define SLV_VIS_HEARTBEAT_MS 1000
/*! \brief The sim's admin round-trip bound ([JanusWebRtcVoice] AdminTimeoutMs, §5). */
#define SLV_VIS_ADMIN_TIMEOUT_MS 5000
/*! \brief One feeder tick of scheduling slip ([WebRtcVoice] VisibilityTickMs, §5). */
#define SLV_VIS_TICK_SLIP_MS 250

/*! \brief The §5 constraint: the smallest staleness window a heartbeat interval allows,
 * 2 x interval + 5000 + 250. For the sim's 1000 ms interval that is 7250 ms. */
static inline uint64_t slv_vis_stale_min_ms(uint64_t interval_ms) {
	return 2u * interval_ms + SLV_VIS_ADMIN_TIMEOUT_MS + SLV_VIS_TICK_SLIP_MS;
}

/*! \brief Parse a room_epoch: exactly 16 hex digits (the sim sends lowercase), not all zero (0 means
 * "no epoch" at the mixer). Returns 1 and sets *out on success, 0 otherwise. */
static inline int slv_vis_parse_epoch(const char *s, uint64_t *out) {
	if(s == NULL || strlen(s) != 16)
		return 0;
	uint64_t v = 0;
	for(int i = 0; i < 16; i++) {
		char c = s[i];
		unsigned d;
		if(c >= '0' && c <= '9')
			d = (unsigned)(c - '0');
		else if(c >= 'a' && c <= 'f')
			d = (unsigned)(c - 'a' + 10);
		else if(c >= 'A' && c <= 'F')
			d = (unsigned)(c - 'A' + 10);
		else
			return 0;
		v = (v << 4) | d;
	}
	if(v == 0)
		return 0;
	if(out != NULL)
		*out = v;
	return 1;
}

/*! \brief Format an epoch as the wire carries it: 16 lowercase hex digits ("0000000000000000" = none). */
static inline void slv_vis_format_epoch(uint64_t epoch, char out[17]) {
	snprintf(out, 17, "%016" PRIx64, epoch);
}

/*! \brief The §1.1 adoption rule applied to one message carrying room_epoch. */
typedef enum slv_vis_epoch_verdict {
	SLV_VIS_EPOCH_EQUAL = 0,   /*!< the stored authority: normal processing */
	SLV_VIS_EPOCH_ADOPT,       /*!< greater than stored, or nothing stored: adopt, disarm every record */
	SLV_VIS_EPOCH_TAKEOVER,    /*!< less than stored, but the stored authority is stale: adopt, disarm every record */
	SLV_VIS_EPOCH_STALE,       /*!< less than stored while the stored authority is fresh: reject, change nothing */
} slv_vis_epoch_verdict;

/*! \brief Is the stored authority stale: no accepted message within the window, or it said "stopping"
 * (the plugin then zeroes last_accept_us)? */
static inline int slv_vis_authority_stale(int64_t last_accept_us, int64_t now_us, int64_t stale_us) {
	return last_accept_us == 0 || now_us - last_accept_us > stale_us;
}

static inline slv_vis_epoch_verdict slv_vis_epoch_decide(uint64_t stored, int64_t last_accept_us,
		int64_t now_us, int64_t stale_us, uint64_t incoming) {
	if(stored == 0 || incoming > stored)
		return SLV_VIS_EPOCH_ADOPT;
	if(incoming == stored)
		return SLV_VIS_EPOCH_EQUAL;
	return slv_vis_authority_stale(last_accept_us, now_us, stale_us) ? SLV_VIS_EPOCH_TAKEOVER : SLV_VIS_EPOCH_STALE;
}

static inline const char *slv_vis_epoch_verdict_str(slv_vis_epoch_verdict v) {
	switch(v) {
		case SLV_VIS_EPOCH_EQUAL:    return "equal";
		case SLV_VIS_EPOCH_ADOPT:    return "adopted";
		case SLV_VIS_EPOCH_TAKEOVER: return "takeover";
		case SLV_VIS_EPOCH_STALE:    return "stale_epoch";
		default:                     return "?";
	}
}

/*! \brief The §4 listener rule: which row a display stands in.
 *   1: no record (unarmed)                        -> silence
 *   2: record from another epoch                  -> silence
 *   3: current epoch, marked stale or not
 *      confirmed within the window                -> silence
 *   4: current epoch, fresh                       -> pass (subject to the exclusion and mute sets)
 * The pair rule is row(L) == 4 && row(S) == 4. */
static inline int slv_vis_row(int armed, uint64_t record_epoch, uint64_t auth_epoch, int64_t confirmed_us,
		int stale, int64_t now_us, int64_t stale_us) {
	if(!armed)
		return 1;
	if(auth_epoch == 0 || record_epoch != auth_epoch)
		return 2;
	if(stale || now_us - confirmed_us > stale_us)
		return 3;
	return 4;
}

/*! \brief would_silence_pairs for a room of n participants of which `good` stand in row 4: the ordered
 * (listener, source) pairs, listener != source, where either side is not row 4. */
static inline uint64_t slv_vis_would_silence_pairs(uint64_t n, uint64_t good) {
	uint64_t all = n < 2 ? 0 : n * (n - 1);
	uint64_t ok = good < 2 ? 0 : good * (good - 1);
	return all - ok;
}

/*! \brief Result of reading one knob from the environment. */
typedef enum slv_vis_knob_status {
	SLV_VIS_KNOB_OK = 0,        /*!< unset (default used) or a valid value */
	SLV_VIS_KNOB_INVALID,       /*!< not a valid value: ignored, default used (the caller WARNs) */
	SLV_VIS_KNOB_CLAMPED,       /*!< valid but below the §5 constraint: raised to it (the caller WARNs) */
} slv_vis_knob_status;

/*! \brief JS_VIS_FAIL_CLOSED: 1/true/yes/on enable, 0/false/no/off or unset disable; anything else is
 * invalid and disables. */
static inline slv_vis_knob_status slv_vis_fail_closed_from_env(const char *v, int *out) {
	*out = 0;
	if(v == NULL || *v == '\0')
		return SLV_VIS_KNOB_OK;
	if(!strcasecmp(v, "1") || !strcasecmp(v, "true") || !strcasecmp(v, "yes") || !strcasecmp(v, "on")) {
		*out = 1;
		return SLV_VIS_KNOB_OK;
	}
	if(!strcasecmp(v, "0") || !strcasecmp(v, "false") || !strcasecmp(v, "no") || !strcasecmp(v, "off"))
		return SLV_VIS_KNOB_OK;
	return SLV_VIS_KNOB_INVALID;
}

/*! \brief JS_VIS_STALE_MS: an integer 1..SLV_VIS_STALE_MS_MAX; unset or invalid gives the default, and a
 * value below slv_vis_stale_min_ms(SLV_VIS_HEARTBEAT_MS) is clamped up to it. */
static inline slv_vis_knob_status slv_vis_stale_ms_from_env(const char *v, unsigned *out) {
	*out = SLV_VIS_STALE_MS_DEFAULT;
	if(v == NULL || *v == '\0')
		return SLV_VIS_KNOB_OK;
	char *end = NULL;
	long ms = strtol(v, &end, 10);
	if(end == NULL || *end != '\0' || ms <= 0 || ms > SLV_VIS_STALE_MS_MAX)
		return SLV_VIS_KNOB_INVALID;
	uint64_t min = slv_vis_stale_min_ms(SLV_VIS_HEARTBEAT_MS);
	if((uint64_t)ms < min) {
		*out = (unsigned)min;
		return SLV_VIS_KNOB_CLAMPED;
	}
	*out = (unsigned)ms;
	return SLV_VIS_KNOB_OK;
}

#endif /* SLV_VISAUTH_H */
