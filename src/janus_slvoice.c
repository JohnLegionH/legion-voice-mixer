/*! \file    janus_slvoice.c
 * \author   Legion Voice Mixer project
 * \copyright GNU General Public License v3
 * \brief    Janus SLVoice plugin — Phase 1A (hold a WebRTC voice session)
 *
 * \details  \c janus.plugin.slvoice is a spatial voice mixer for OpenSimulator
 * grids speaking the Second Life WebRTC voice protocol. Phase 1A's goal is to
 * make a real Firestorm viewer ESTABLISH AND HOLD a voice session — a stable
 * PeerConnection with a working voice dot and silence. No audio processing yet.
 *
 * The critical piece is full JSEP negotiation INCLUDING the SCTP DataChannel:
 * the viewer's offer carries both m=audio (Opus) and m=application (the SLData
 * DataChannel it creates before negotiation, per docs/webrtc-voice-spec.md §9).
 * Both must be answered. If the m=application line is not answered (port>0,
 * DTLS/SCTP proto), the Janus core logs "Skipping unsupported application media
 * line" (vendor/janus-gateway/src/sdp.c:1625) and the viewer tears the session
 * down and retries — the join/leave loop this phase fixes.
 *
 * We answer the application m-line by calling janus_sdp_generate_answer_mline()
 * with JANUS_SDP_OA_MLINE, JANUS_SDP_APPLICATION — the pattern used by
 * janus_videoroom.c:13113 (janus_textroom is the DataChannel reference for the
 * relay_data/incoming_data/data_ready lifecycle, but it is offer-only, so the
 * answer pattern is modelled on videoroom). No plugin capability flag exists;
 * the core wires up SCTP purely from the answer's accepted application m-line
 * (sdp.c:1611-1637), provided Janus was built with HAVE_SCTP (data_channels:true).
 *
 * Protocol: handle_message() implements a superset of the audiobridge request
 * protocol the OpenSim C# side emits (create/destroy/join/leave/configure/list/
 * listparticipants), with the SAME field names, the SAME response envelope key
 * ("audiobridge"), and the SAME error codes (486 room-exists, 485 no-such-room)
 * so the C# side flips audiobridge <-> slvoice by config alone. See
 * docs/protocol-compat.md and docs/voice/current-architecture.md §3.
 *
 * Phase 1B adds ECHO on the held session: incoming_rtp -> fixed-lag jitter
 * buffer -> Opus decode (48k float) -> 500ms delay ring -> Opus encode (stereo,
 * spec §9 fmtp) -> relay_rtp back to the SAME participant. Real RMS*128 power +
 * VAD flow into the mixer->client SLData batch. Echo is per-participant, toggled
 * by {"echo":true/false} on SLData or auto-started via SLV_ECHO_AUTOSTART.
 * OUT OF SCOPE: cross-participant mixing and spatialization (Phase 2).
 *
 * Written against the Janus 1.4.1 plugin API (JANUS_PLUGIN_API_VERSION 106);
 * the vendored headers and in-tree plugins are the authority.
 */

#include <inttypes.h>
#include <string.h>
#include <arpa/inet.h>   /* htons/htonl for the outgoing RTP header */

#include <jansson.h>

#include <janus/plugins/plugin.h>
#include <janus/debug.h>
#include <janus/config.h>
#include <janus/mutex.h>
#include <janus/refcount.h>
#include <janus/utils.h>
#include <janus/sdp-utils.h>
#include <janus/rtp.h>   /* JANUS_RTP_EXTMAP_MID / JANUS_RTP_EXTMAP_AUDIO_LEVEL, janus_rtp_payload */

#include <opus/opus.h>   /* decode/encode */
#include <math.h>        /* sqrtf for RMS */

#include "sldata.h"
#include "visbatch.h"    /* Phase 3a: server-to-server visibility batch parser (unit-tested) */
#include "deferred.h"    /* join-window fix: per-room store of columns deferred for a not-yet-joined listener */
#include "roster.h"      /* Phase 3a: single-source-of-truth exclusion predicate (unit-tested) */
#include "mixer/mix.h"   /* Phase 2: pure N-minus-one mixing math (unit-tested) */
#include "mixer/vec3.h"  /* Phase 3b: pure 3D vector math (geometry snapshot / leash) */
#include "mixer/azimuth.h" /* Phase 3b item 4: horizontal azimuth of a source (pan input) */
#include "mixer/pan.h"     /* Phase 3b item 4: constant-power stereo pan gains */

/* Plugin information */
#define JANUS_SLVOICE_VERSION         9
#define JANUS_SLVOICE_VERSION_STRING  "0.9.0"
#define JANUS_SLVOICE_DESCRIPTION     "Spatial voice mixer for OpenSimulator, speaking the Second Life WebRTC voice protocol (Phase 3b: per-room N-minus-one mixing with DTX/VAD cull, per-source mute/gain and encode-skip; sim-authoritative per-listener visibility exclusion; distance culling with hysteresis, distance attenuation and constant-power azimuth panning from viewer geometry, with a camera-position leash; no HRTF, ITD, distance tiers or azimuth binning yet; echo remains a per-participant diagnostic override)."
#define JANUS_SLVOICE_NAME            "Legion SLVoice mixer"
#define JANUS_SLVOICE_AUTHOR          "Legion Voice Mixer project"
#define JANUS_SLVOICE_PACKAGE         "janus.plugin.slvoice"

/* Application-level error codes — MUST match janus.plugin.audiobridge's numeric
 * codes: the OpenSim C# side reads `error_code` and treats 486 ("room exists")
 * as success (docs/voice/current-architecture.md §3.3). Numbers verbatim from
 * vendor/janus-gateway/src/plugins/janus_audiobridge.c. */
#define JANUS_SLVOICE_ERROR_NO_MESSAGE      480
#define JANUS_SLVOICE_ERROR_INVALID_JSON    481
#define JANUS_SLVOICE_ERROR_INVALID_REQUEST 482
#define JANUS_SLVOICE_ERROR_MISSING_ELEMENT 483
#define JANUS_SLVOICE_ERROR_INVALID_ELEMENT 484
#define JANUS_SLVOICE_ERROR_NO_SUCH_ROOM    485
#define JANUS_SLVOICE_ERROR_ROOM_EXISTS     486
#define JANUS_SLVOICE_ERROR_NOT_JOINED      487
#define JANUS_SLVOICE_ERROR_ALREADY_JOINED  491
#define JANUS_SLVOICE_ERROR_INVALID_SDP     493
/* Room at capacity (SLV_MAX_MIX). 495 is free in audiobridge's scheme (it tops out at
 * 494), so this is unambiguously ours — the sim connector keys on it to return HTTP
 * 409 Conflict, which the viewer maps to ERROR_CHANNEL_FULL. Distinct from every other
 * join failure so an unrelated error never reads as "room full". */
#define JANUS_SLVOICE_ERROR_ROOM_FULL       495
#define JANUS_SLVOICE_ERROR_UNKNOWN         499

/* Mixer->client SLData power/VAD batch cadence (spec §9: ~100ms). */
#define SLV_POWER_TICK_MS   100

/* ---- Phase 2 mixer / audio constants --------------------------------------
 * Everything runs at Opus fullband stereo (matching the negotiated
 * stereo=1;sprop-stereo=1 fmtp), so decode/encode channel counts are fixed. */
#define SLV_RATE            48000
#define SLV_CHANNELS        2
#define SLV_TICK_MS         20                                    /* mixer tick period (mixer.h SLV_TICK_INTERVAL_MS) */
#define SLV_FRAME_SAMPLES   ((SLV_RATE/1000)*SLV_TICK_MS)         /* 960 per channel (20ms) */
#define SLV_FRAME_TOTAL     (SLV_FRAME_SAMPLES*SLV_CHANNELS)      /* 1920 interleaved */
#define SLV_ECHO_DELAY_MS   500
#define SLV_DELAY_SAMPLES   ((SLV_RATE/1000)*SLV_ECHO_DELAY_MS)   /* 24000 per channel */
#define SLV_DECODE_MAX      5760    /* per-channel decode headroom (120ms = max Opus frame) */
#define SLV_RING_SAMPLES    (SLV_DELAY_SAMPLES + SLV_DECODE_MAX + SLV_FRAME_SAMPLES)
#define SLV_RTP_OUT_MAX     1500
/* Fixed-lag jitter buffer: reorder window, drained one frame per tick. */
#define SLV_JB_SLOTS        128     /* ring of slots (~2.5s at 20ms) */
#define SLV_JB_LAG          3       /* play SLV_JB_LAG packets behind the newest (~60ms) */
#define SLV_JB_MAX_LEAD     16      /* resync if the playout cursor falls this far behind (DTX resume) */
#define SLV_JB_PAYLOAD_MAX  1500    /* max Opus payload bytes per slot */
/* Energy threshold for the simple VAD (RMS of decoded float, 0..1). Also the
 * encode-skip silence floor for a listener's mix. */
#define SLV_VAD_RMS         0.02f
/* DTX/VAD cull release hold: keep a talker in the active set this long after
 * its last RTP, so a DTX gap / brief pause does not chop the next word onset
 * (spec §5: "100-200 ms release hold"). */
#define SLV_VAD_RELEASE_MS  150
/* Per-source gain conversion: the viewer sends ug = volume * 220
 * (PEER_GAIN_CONVERSION_FACTOR in llvoicewebrtc.cpp), so linear = ug/220. */
#define SLV_GAIN_FACTOR     220.0
#define SLV_GAIN_MAX        4.0     /* clamp recovered per-source gain (sanity) */
/* Max participants folded into one tick's mix, AND the room admission ceiling
 * (enforced at join — see the ROOM_FULL check). One number governs both: the mix
 * arrays are sized to it, so admitting past it is exactly what the old silent tick-
 * time truncation did. 110 matches Second Life's per-region limit; the measurement
 * (docs/voice/scaling-assessment.md) shows it fits under realistic spread geometry,
 * though NOT under all-audible clustering. Overridable with -DSLV_MAX_MIX=N for
 * measurement builds; raising it exposes the O(N) encode and O(N^2) setup costs. */
#ifndef SLV_MAX_MIX
#define SLV_MAX_MIX         110
#endif
/* Opus encoder tuning for the per-listener mix. Both are overridable at build time
 * (-DSLV_OPUS_COMPLEXITY=N / -DSLV_OPUS_BITRATE=N) for measurement builds that sweep
 * encode cost, mirroring SLV_MAX_MIX; production values are unchanged. Complexity is
 * the dominant CPU lever of the two (docs/voice/scaling-assessment.md). */
#ifndef SLV_OPUS_COMPLEXITY
#define SLV_OPUS_COMPLEXITY 9        /* 0..10; 10 is max. */
#endif
/* NUMERIC-ONLY: OPUS_SET_BITRATE also accepts the sentinels OPUS_AUTO and
 * OPUS_BITRATE_MAX, which this integer define does NOT carry — do not expect them
 * to work through it. */
#ifndef SLV_OPUS_BITRATE
#define SLV_OPUS_BITRATE    64000    /* bits/sec; spec §5: listener mix 64-96kbps stereo */
#endif
/* Warn if a single tick takes longer than this (spec §4.1 mix-deadline). */
#define SLV_TICK_WARN_MS    15.0

/* ---- Phase 3b spatial constants — DEFAULTS ONLY (Amendment 8) ---------------
 * Viewer geometry (sp/sh/lp/lh) arrives scaled ×100 — an int of value×100 — so a
 * stored coordinate is centimetres (docs/voice/phase3b-design-brief.md "Resolved:
 * the geometry unit"; viewer llvoicewebrtc.cpp:1239-1266). The DSP works in that
 * ×100 stored unit.
 *
 * These five values are now RUNTIME-configurable via process-wide [general] jcfg
 * keys (parsed in janus_slvoice_init). The macros below are DEFAULTS ONLY: given in
 * metres, consumed in exactly two places — the slv_spatial static initialiser and
 * janus_slvoice_load_spatial_settings — and NEVER at a tick use site. The tick reads
 * slv_spatial.* (stored units); the metres->stored conversion happens ONCE, at parse.
 * There is deliberately NO stored-unit macro any more: its removal makes it a COMPILE
 * ERROR, not a silent unit bug, for the tick to read a constant instead of the setting. */
#define SLV_GEOM_SCALE           100.0   /* stored unit = metres × 100 */
#define SLV_LEASH_DIST_M         50.0    /* §7.1 camera leash = MAX_AUDIO_DIST (llvoicewebrtc.cpp:92) */
#define SLV_CUTOFF_DIST_M        60.0    /* audible cutoff: a source beyond this is culled */
#define SLV_READD_DIST_M         58.0    /* hysteresis re-add: un-cull only inside this (< cutoff) */
#define SLV_REF_DIST_M           10.0    /* full volume within this radius */
#define SLV_FALLOFF_EXP_DEFAULT  2.0     /* falloff shaping exponent (dimensionless; do not scale) */

/* Runtime-configurable spatial DSP settings (Amendment 8). The tick reads THIS
 * struct, never a macro. The four distances are in stored units (metres ×
 * SLV_GEOM_SCALE); falloff_exp is dimensionless. Pre-initialised to the compiled
 * defaults, so with no config file — or no spatial keys — every value is
 * byte-identical to the previous build. Overwritten only by
 * janus_slvoice_load_spatial_settings, and only when the whole set validates. */
typedef struct slv_spatial_settings {
	double leash_dist;    /* stored: §7.1 camera-leash radius */
	double cutoff_dist;   /* stored: cull cutoff */
	double readd_dist;    /* stored: hysteresis re-add (must be < cutoff) */
	double ref_dist;      /* stored: attenuation reference (must be < cutoff) */
	double falloff_exp;   /* dimensionless: attenuation shaping exponent (must be > 0) */
} slv_spatial_settings;
static slv_spatial_settings slv_spatial = {
	.leash_dist  = SLV_LEASH_DIST_M  * SLV_GEOM_SCALE,   /* 5000 */
	.cutoff_dist = SLV_CUTOFF_DIST_M * SLV_GEOM_SCALE,   /* 6000 */
	.readd_dist  = SLV_READD_DIST_M  * SLV_GEOM_SCALE,   /* 5800 */
	.ref_dist    = SLV_REF_DIST_M    * SLV_GEOM_SCALE,   /* 1000 */
	.falloff_exp = SLV_FALLOFF_EXP_DEFAULT,              /* 2.0  */
};

/* Packet-level logging is gated behind a compile-time flag so the RTP-ingest
 * path stays silent in production. Build with -DSLV_DEBUG_MEDIA to enable. */
#ifdef SLV_DEBUG_MEDIA
#define SLV_MEDIA_LOG(...) JANUS_LOG(LOG_HUGE, __VA_ARGS__)
#else
#define SLV_MEDIA_LOG(...) do {} while(0)
#endif

/* Plugin methods (forward declarations, matching plugin.h exactly) */
janus_plugin *create(void);
int janus_slvoice_init(janus_callbacks *callback, const char *config_path);
void janus_slvoice_destroy(void);
int janus_slvoice_get_api_compatibility(void);
int janus_slvoice_get_version(void);
const char *janus_slvoice_get_version_string(void);
const char *janus_slvoice_get_description(void);
const char *janus_slvoice_get_name(void);
const char *janus_slvoice_get_author(void);
const char *janus_slvoice_get_package(void);
void janus_slvoice_create_session(janus_plugin_session *handle, int *error);
struct janus_plugin_result *janus_slvoice_handle_message(janus_plugin_session *handle, char *transaction, json_t *message, json_t *jsep);
json_t *janus_slvoice_handle_admin_message(json_t *message);
void janus_slvoice_setup_media(janus_plugin_session *handle);
void janus_slvoice_incoming_rtp(janus_plugin_session *handle, janus_plugin_rtp *packet);
void janus_slvoice_incoming_rtcp(janus_plugin_session *handle, janus_plugin_rtcp *packet);
void janus_slvoice_incoming_data(janus_plugin_session *handle, janus_plugin_data *packet);
void janus_slvoice_data_ready(janus_plugin_session *handle);
void janus_slvoice_slow_link(janus_plugin_session *handle, int mindex, gboolean video, gboolean uplink);
void janus_slvoice_hangup_media(janus_plugin_session *handle);
void janus_slvoice_destroy_session(janus_plugin_session *handle, int *error);
json_t *janus_slvoice_query_session(janus_plugin_session *handle);

/* Plugin setup */
static janus_plugin janus_slvoice_plugin =
	JANUS_PLUGIN_INIT (
		.init = janus_slvoice_init,
		.destroy = janus_slvoice_destroy,

		.get_api_compatibility = janus_slvoice_get_api_compatibility,
		.get_version = janus_slvoice_get_version,
		.get_version_string = janus_slvoice_get_version_string,
		.get_description = janus_slvoice_get_description,
		.get_name = janus_slvoice_get_name,
		.get_author = janus_slvoice_get_author,
		.get_package = janus_slvoice_get_package,

		.create_session = janus_slvoice_create_session,
		.handle_message = janus_slvoice_handle_message,
		.handle_admin_message = janus_slvoice_handle_admin_message,
		.setup_media = janus_slvoice_setup_media,
		.incoming_rtp = janus_slvoice_incoming_rtp,
		.incoming_rtcp = janus_slvoice_incoming_rtcp,
		.incoming_data = janus_slvoice_incoming_data,
		.data_ready = janus_slvoice_data_ready,
		.slow_link = janus_slvoice_slow_link,
		.hangup_media = janus_slvoice_hangup_media,
		.destroy_session = janus_slvoice_destroy_session,
		.query_session = janus_slvoice_query_session,
	);

/* Plugin creator */
janus_plugin *create(void) {
	JANUS_LOG(LOG_VERB, "%s created!\n", JANUS_SLVOICE_NAME);
	return &janus_slvoice_plugin;
}

/* Useful stuff */
static volatile gint initialized = 0, stopping = 0;
static janus_callbacks *gateway = NULL;
static GThread *handler_thread = NULL;   /* async request handler */
static GThread *sender_thread = NULL;    /* ~100ms mixer->client SLData ticker */
static void *janus_slvoice_handler(void *data);
static void *janus_slvoice_sender(void *data);

/* When set (SLV_ECHO_AUTOSTART / general.echo_autostart), echo is enabled for
 * every participant the moment its PeerConnection is up — so a stock viewer,
 * which cannot send the {"echo":true} SLData toggle, still hears itself. */
static gboolean echo_autostart = FALSE;

/* ---- Rooms (slv_regions) and participants (folded into the session) -------
 * One WebRTC peer = one participant. A room is a lightweight membership +
 * metadata holder keyed by the room number the C# side computes (CalcRoomNumber).
 * The per-region mix thread (src/mixer/mixer.h) is Phase 2. */
typedef struct janus_slvoice_room {
	guint64 room_id;
	char *description;
	gboolean is_private;
	gboolean permanent;         /* loaded from static config (not saved on destroy) */
	guint32 sampling_rate;      /* advertised in list; internal mix rate is fixed 48k */
	gboolean spatial_audio;
	GHashTable *participants;    /* guint64* user_id -> janus_slvoice_session* (borrowed) */
	janus_mutex mutex;

	/* ---- Phase 2 per-region mix tick (the mixer.h slv_region model, realised
	 * on this room). One dedicated 20ms thread per room; no shared mix thread. */
	GThread *ticker;
	volatile gint tick_running;  /* set FALSE to ask the tick thread to exit */
	guint64 tick_seq;            /* monotonic tick counter */
	/* Tick-duration histogram (guarded by mutex), buckets in ms:
	 * [0]<5 [1]5-10 [2]10-15 [3]15-20 [4]>=20. */
	guint64 tick_hist[5];
	guint64 tick_overruns;       /* ticks exceeding SLV_TICK_WARN_MS */
	double tick_last_ms;
	double tick_max_ms;

	/* ---- Phase 3a per-listener visibility feed (server-to-server peer_ctl_batch).
	 * Guarded by room->mutex (the tick's whole-pass lock) — see
	 * janus_slvoice_apply_visbatch for the atomicity contract. */
	guint64 vis_epoch;             /* +1 per applied batch (feed liveness / ordering) */
	gint64 vis_last_ts;            /* monotonic us of the last applied batch (0 = none) */
	slv_vis_op vis_last_mode;      /* op of the last applied batch (add/remove/replace) */
	gboolean vis_have_batch;       /* at least one batch applied */
	guint64 vis_joins_since_snapshot; /* participant joins since the last REPLACE (re-derive lag) */
	/* Dropped LISTENER ENTRIES: exclusion entries whose listener display matched no session in
	 * the room (nmatch==0 in apply_visbatch), so the entry was skipped. ROOM-level and named
	 * "..._listener_entries" precisely so it cannot be confused with the per-session
	 * excluded_entries counter. A correct drop (the sim naming a listener who just left) and a
	 * wrong one (a listener that IS here under a display the feed doesn't match) are
	 * indistinguishable without this — the §M investigation's missing instrument. */
	guint64 vis_dropped_listener_entries;      /* cumulative since room creation */
	guint64 vis_last_batch_dropped_listeners;  /* drops in the most recently applied batch */

	/* Join-window fix (src/deferred.h): columns DEFERRED for a listener that named this
	 * room in a batch but is not yet a participant (nmatch==0). Replayed and removed by
	 * the join branch the instant that listener joins, before any presence derived from
	 * them is revealed. Guarded by room->mutex, like the vis_* fields above. */
	slv_deferred_store vis_deferred;

	volatile gint destroyed;
	janus_refcount ref;
} janus_slvoice_room;

/* Persistent per-listener control for one OTHER participant, keyed by that
 * participant's agent UUID. Built up incrementally from the viewer's per-source
 * SLData ({"m":{uuid:bool}} / {"ug":{uuid:val}}); a mute message must not wipe a
 * prior gain, so this map lives on the session, not on the per-message parse. */
typedef struct slv_peer_ctl {
	char uuid[SLV_UUID_LEN]; /* target participant agent UUID */
	gboolean muted;          /* this listener PERSONALLY muted the target (viewer SLData "m") */
	gboolean mod_muted;      /* the SIM moderation-muted the target for this listener (mute channel,
	                          * Option A). Kept SEPARATE from `muted` so clearing a moderation mute
	                          * never wipes the viewer's own personal mute, and vice versa. The mix
	                          * silences the source when EITHER is set. */
	gboolean has_gain;       /* a per-source gain was set for the target */
	float gain;              /* linear gain (ug/220), clamped */
} slv_peer_ctl;

/* Per-listener distance-cull hysteresis for one OTHER participant, keyed by that
 * participant's agent UUID (Phase 3b item 2). A PARALLEL structure to peer_ctl: it
 * covers every audible source, not just viewer-adjusted ones, so it is sized to the
 * participant cap and LRU-evicts the least-recently-seen slot when full (brief
 * Amendment 1 pins UUID-keyed + SLV_MAX_MIX-sized; Amendment 2 pins the eviction). */
typedef struct slv_cull_hyst {
	char uuid[SLV_UUID_LEN]; /* source participant agent UUID */
	gboolean culled;         /* current cull state (the hysteresis latch) */
	guint64 seen_tick;       /* room tick_seq when last matched (LRU stamp) */
} slv_cull_hyst;

typedef struct janus_slvoice_session {
	janus_plugin_session *handle;
	guint64 user_id;             /* participant id; 0 until joined */
	janus_slvoice_room *room;    /* ref held while joined; NULL otherwise (guarded by mutex) */
	char *display;               /* display name from join = the agent UUID string (§3.2) */
	int opus_pt;                 /* negotiated Opus payload type; -1 until join */
	gboolean has_datachannel;    /* offer contained an m=application line */
	gboolean dc_answered;        /* our answer accepted the m=application line */
	gint64 created_ts;           /* monotonic us at session create (for uptime) */

	volatile gint webrtc_up;     /* setup_media(1) / hangup_media(0) — coarse ICE/DTLS up */
	volatile gint dc_open;       /* data_ready seen: the data channel is writable */

	/* ---- Media state (guarded by mutex; ALL buffers allocated at join in
	 * janus_slvoice_media_alloc_locked, freed at leave/destroy — nothing is
	 * allocated in the tick or per packet). ---- */
	gboolean media_ready;        /* buffers + codecs allocated */
	OpusDecoder *dec;            /* decodes THIS participant's input (as a source) */
	OpusEncoder *enc;            /* encodes THIS participant's output (its mix/echo) */
	/* Fixed-lag jitter buffer (reorder window), drained one frame per tick. */
	unsigned char *jb;           /* SLV_JB_SLOTS * SLV_JB_PAYLOAD_MAX */
	uint16_t *jb_seq;            /* SLV_JB_SLOTS: seq stored in each slot */
	int *jb_len;                 /* SLV_JB_SLOTS: payload length, 0 = empty */
	uint16_t jb_next;            /* next seq to play out (playout cursor) */
	uint16_t jb_newest;          /* newest seq received */
	uint16_t jb_first_seq;       /* first seq ever received (initial prime point) */
	gboolean jb_have_first;      /* have we seen any packet? */
	gboolean jb_primed;          /* has playout started? */
	/* Per-tick decoded source frame (tick-owned: written only by the tick's
	 * pass 1, read by pass 2 for other listeners' mixes — no lock needed there). */
	float *decbuf;               /* SLV_DECODE_MAX * SLV_CHANNELS decoded PCM this tick */
	int dec_samples;             /* per-channel samples of real audio this tick (0 = silent) */
	volatile gint active;        /* DTX/VAD cull: in the active talker set (150ms release hold) */
	gint64 last_rtp_us;          /* monotonic us of the last inbound RTP (for the cull) */
	gboolean decode_ok;          /* last decode attempt succeeded */
	/* Phase 3b geometry snapshot (tick-owned: written only by the tick's pass 1
	 * under s->mutex, read by pass 2's per-listener DSP — no lock needed there,
	 * the same discipline as decbuf above). Copied from last_data each tick and,
	 * for lp, leash-clamped once here so no listener ever reads an unclamped
	 * camera. Nothing consumes these yet (Phase 3b slice one is snapshot-only). */
	slv_vec3 snap_sp;            /* self (avatar) position, stored ×100 (cm) */
	slv_vec3 snap_lp;            /* listener (camera) position, leash-clamped, ×100 */
	slv_quat snap_lh;            /* listener heading/orientation, ×100 (identity if absent) */
	gboolean snap_valid;         /* sp AND lp present this tick -> snap usable for distance */
	/* Phase 3b item 2: per-listener distance-cull hysteresis, keyed by source UUID,
	 * sized to the participant cap, LRU-evicted (brief Amendments 1 & 2). Inline, no
	 * allocation — a parallel to peer_ctl above; touched only in pass 2 under mutex. */
	slv_cull_hyst cull_hyst[SLV_MAX_MIX];
	int n_cull_hyst;
	/* 500ms echo delay ring (per-listener echo override) + encode scratch. */
	float *ring;                 /* SLV_RING_SAMPLES * SLV_CHANNELS */
	int ring_wpos;               /* write cursor, per-channel sample index */
	float *framebuf;             /* SLV_FRAME_SAMPLES * SLV_CHANNELS frame handed to the encoder */
	unsigned char *outbuf;       /* SLV_RTP_OUT_MAX: 12B RTP header + Opus payload */
	uint16_t out_seq;            /* advanced only on SENT packets (DTX-correct) */
	uint32_t out_ts;             /* advanced every tick by SLV_FRAME_SAMPLES */
	uint32_t out_ssrc;
	int silent_ticks;            /* consecutive silent (encode-skipped) ticks */
	gboolean first_frame_logged; /* log first-frame-decoded once */

	/* Echo override (diagnostics): when set, this participant hears its own
	 * audio delayed 500ms INSTEAD of the N-1 mix. */
	volatile gint echo_active;

	/* Persistent per-source mute/gain this listener has applied to others. */
	slv_peer_ctl peer_ctl[SLV_MAX_PEER_ADJ];
	int n_peer_ctl;

	/* Phase 3a per-listener visibility exclusions: a set of TARGET (source) agent
	 * UUID strings this listener must not hear or see. A PARALLEL structure to
	 * peer_ctl (not folded into it) because it is O(N) not O(few), sim-sourced not
	 * viewer-sourced, and wants O(1) membership in the hot mix loop. Both the mix
	 * loop and the roster/presence path read this ONE set (single source of truth).
	 * Written under room->mutex + session->mutex; read by the tick / query_session
	 * under session->mutex and by the sender / presence under room->mutex. Keys are
	 * g_strdup'd (freed on removal / destroy). */
	GHashTable *excluded;

	/* Diagnostics (guarded by mutex except where atomic) */
	guint64 rtp_in_count;        /* RTP packets ingested */
	guint64 frames_decoded;      /* Opus frames decoded (incl. PLC) */
	guint64 frames_encoded;      /* Opus frames encoded for this listener's output */
	guint64 frames_mixed;        /* ticks this listener received a non-silent mix */
	guint64 rtp_out_count;       /* output RTP packets relayed */
	guint64 rtp_in_prev, rtp_out_prev; /* last-window snapshots for rate calc */
	double rtp_in_rate;          /* inbound RTP packets/sec (updated ~1s by sender) */
	double rtp_out_rate;         /* outbound RTP packets/sec */
	gint64 rate_ts;              /* monotonic us of the last rate update */
	double last_rms;             /* RMS of the most recent decoded frame (0..1) */
	double last_mix_rms;         /* RMS of this listener's most recent MIXED OUTPUT frame
	                              * (0..1): the N-1 mix (or echo) relayed to it, NOT the
	                              * inbound talker level in last_rms. Diagnostic-only. */
	double last_mix_rms_l;       /* Phase 3b item 4: RMS of the LEFT channel of that same
	                              * frame (even interleaved samples). Diagnostic-only. */
	double last_mix_rms_r;       /* Phase 3b item 4: RMS of the RIGHT channel (odd samples).
	                              * last_mix_rms sums both and cannot detect an L/R swap;
	                              * these two can (Amendment 4/5). Diagnostic-only. */
	volatile gint power_p;       /* RMS*128 clamped 0..127 (for the SLData batch) */
	volatile gint vad;           /* simple energy VAD 0/1 (for the SLData batch) */
	guint64 data_msgs_received;  /* SLData messages received on the data channel */
	guint64 presence_pushed;     /* M-A2A-1: presence/mute JSON objects relayed to THIS session as
	                              * recipient (push_presence, join backlog, transition/mute emits). */
	guint64 presence_dropped_dc_closed; /* M-A2A-1: presence objects DROPPED because this recipient's
	                              * dc_open was 0. Post-fence this should only ever count a genuinely
	                              * dead channel (hangup_media cleared dc_open), never the join race.
	                              * Both counters incremented under room->mutex (every relay_json
	                              * caller holds it); read unlocked by query_session like
	                              * rtp_in_count (torn values only garble a diagnostic). */
	slv_sldata last_data;        /* latest parsed SLData values */
	unsigned last_data_fields;   /* fields_seen from the last SLData */

	janus_mutex mutex;
	volatile gint hangingup;
	volatile gint destroyed;
	janus_refcount ref;
} janus_slvoice_session;

static GHashTable *sessions = NULL;   /* janus_plugin_session* -> janus_slvoice_session* */
static janus_mutex sessions_mutex = JANUS_MUTEX_INITIALIZER;
static GHashTable *rooms = NULL;      /* guint64* -> janus_slvoice_room* */
static janus_mutex rooms_mutex = JANUS_MUTEX_INITIALIZER;

/* Async message queue (all requests handled off-thread, like audiobridge, so
 * we can push a JSEP answer via push_event rather than a synchronous result) */
typedef struct janus_slvoice_message {
	janus_plugin_session *handle;
	char *transaction;
	json_t *message;
	json_t *jsep;
} janus_slvoice_message;
static GAsyncQueue *messages = NULL;
static janus_slvoice_message exit_message;

/* Media helpers (defined in the media section; forward-declared so the
 * lifecycle code above can allocate/free and toggle echo). All require
 * session->mutex held. */
static gboolean janus_slvoice_media_alloc_locked(janus_slvoice_session *s);
static void janus_slvoice_media_free_locked(janus_slvoice_session *s);
static void janus_slvoice_echo_start_locked(janus_slvoice_session *s);
static void janus_slvoice_echo_stop_locked(janus_slvoice_session *s);

/* Per-room mix tick thread lifecycle (defined in the media section). */
static gboolean janus_slvoice_room_start(janus_slvoice_room *room);
static void janus_slvoice_room_stop(janus_slvoice_room *room);

/* ---- Room helpers -------------------------------------------------------- */

static void janus_slvoice_room_free(const janus_refcount *ref) {
	janus_slvoice_room *room = janus_refcount_containerof(ref, janus_slvoice_room, ref);
	JANUS_LOG(LOG_VERB, "[%s] Freeing room %"PRIu64"\n", JANUS_SLVOICE_PACKAGE, room->room_id);
	if(room->participants)
		g_hash_table_destroy(room->participants);
	slv_deferred_free_all(&room->vis_deferred);   /* free any columns still deferred at teardown */
	g_free(room->description);
	g_free(room);
}

static janus_slvoice_room *janus_slvoice_room_create(guint64 id, const char *desc,
		gboolean is_private, guint32 rate, gboolean spatial, gboolean permanent) {
	janus_slvoice_room *room = g_malloc0(sizeof(janus_slvoice_room));
	room->room_id = id;
	room->description = desc ? g_strdup(desc) : g_strdup_printf("Region %"PRIu64, id);
	room->is_private = is_private;
	room->sampling_rate = rate ? rate : 48000;
	room->spatial_audio = spatial;
	room->permanent = permanent;
	/* keys are heap guint64 (freed by the table); values are borrowed sessions */
	room->participants = g_hash_table_new_full(g_int64_hash, g_int64_equal, g_free, NULL);
	janus_mutex_init(&room->mutex);
	janus_refcount_init(&room->ref, janus_slvoice_room_free);
	slv_deferred_init(&room->vis_deferred);   /* empty deferred store (g_malloc0 already zeroed it) */
	/* Start the per-region 20ms mix tick thread (the mixer.h slv_region model). */
	janus_slvoice_room_start(room);
	return room;
}

/* Look up a room and take a reference. Caller must janus_refcount_decrease. */
static janus_slvoice_room *janus_slvoice_room_ref_by_id(guint64 id) {
	janus_slvoice_room *room = NULL;
	janus_mutex_lock(&rooms_mutex);
	room = g_hash_table_lookup(rooms, &id);
	if(room != NULL && !g_atomic_int_get(&room->destroyed)) {
		janus_refcount_increase(&room->ref);
	} else {
		room = NULL;
	}
	janus_mutex_unlock(&rooms_mutex);
	return room;
}

/* Remove a session from its room (if any) and drop the room ref. The session
 * that transitions room from non-NULL to NULL is the one that unrefs — making
 * leave / destroy_session / room-destroy races safe. */
static void janus_slvoice_leave_room(janus_slvoice_session *session) {
	janus_slvoice_room *room = NULL;
	guint64 uid = 0;
	janus_mutex_lock(&session->mutex);
	room = session->room;
	uid = session->user_id;
	session->room = NULL;
	janus_mutex_unlock(&session->mutex);
	if(room == NULL)
		return;
	janus_mutex_lock(&room->mutex);
	g_hash_table_remove(room->participants, &uid);
	janus_mutex_unlock(&room->mutex);
	/* Clear the sim-sourced exclusion set. It is membership state for the room we
	 * just left; a session that rejoins must not carry it into the next room.
	 * Emptied, NOT destroyed: apply_visbatch dereferences L->excluded directly and
	 * would crash on a NULL table, so only session_free frees it. remove_all runs
	 * the g_free key destructor the table was created with, so no key leaks. Done
	 * AFTER the participants removal so a batch mid-apply under room->mutex cannot
	 * repopulate the set we just cleared. */
	janus_mutex_lock(&session->mutex);
	if(session->excluded != NULL)
		g_hash_table_remove_all(session->excluded);
	janus_mutex_unlock(&session->mutex);
	janus_refcount_decrease(&room->ref);
}

/* ---- Mixer->client SLData (data channel send) ---------------------------- */

/* Relay a JSON object to one participant over the data channel. Text, default
 * label (janus_textroom.c:1632 uses .label=NULL .protocol=NULL .binary=FALSE).
 * relay_data copies the buffer synchronously, so we free it right after. Only
 * sends once the channel is writable (data_ready has fired). */
static void janus_slvoice_relay_json(janus_slvoice_session *s, json_t *obj) {
	if(s == NULL || obj == NULL || gateway == NULL)
		return;
	if(!g_atomic_int_get(&s->dc_open)) {
		/* M-A2A-1: count the drop. Every caller holds room->mutex, and since the
		 * data_ready fence (janus_slvoice_data_ready) sets dc_open under that same
		 * mutex, a drop here can no longer be the join race — only a channel that
		 * is genuinely not (or no longer) writable. */
		s->presence_dropped_dc_closed++;
		return;
	}
	s->presence_pushed++;
	char *text = json_dumps(obj, JSON_COMPACT);
	if(text == NULL)
		return;
	size_t len = strlen(text);
	if(len < 65536) {
		janus_plugin_data data = { .label = NULL, .protocol = NULL, .binary = FALSE,
			.buffer = text, .length = (uint16_t)len };
		gateway->relay_data(s->handle, &data);
	}
	free(text);
}

/* Per-peer presence notice: {"<who>":{"j"|"l":true}} to every OTHER participant
 * in the room. `who` is the joining/leaving participant's display (agent UUID).
 * Caller must NOT hold room->mutex. */
static void janus_slvoice_push_presence(janus_slvoice_room *room, const char *who, gboolean join) {
	if(room == NULL || who == NULL)
		return;
	json_t *entry = json_object();
	json_t *sub = json_object();
	/* The viewer requires "j" to be an OBJECT {"p":<primary>} (it checks
	 * is_object and reads j.p as the primary-server flag,
	 * llvoicewebrtc.cpp OnDataReceivedImpl); a bare boolean "j" is rejected and
	 * the join is dropped. "l" (leave) is a plain boolean. For a single-region
	 * flat room every participant's primary server is us, so p=true. */
	if(join) {
		json_t *jd = json_object();
		json_object_set_new(jd, "p", json_true());
		json_object_set_new(sub, "j", jd);
	} else {
		json_object_set_new(sub, "l", json_true());
	}
	json_object_set_new(entry, who, sub);
	janus_mutex_lock(&room->mutex);
	/* Phase 3a: a join running ahead of the visibility feed is a re-derive-pending
	 * event; count it so query_session shows how far the roster leads the feed. */
	if(join)
		room->vis_joins_since_snapshot++;
	GHashTableIter iter;
	gpointer value;
	g_hash_table_iter_init(&iter, room->participants);
	while(g_hash_table_iter_next(&iter, NULL, &value)) {
		janus_slvoice_session *p = value;
		if(p->display != NULL && !strcmp(p->display, who))
			continue;   /* skip the subject itself */
		/* Phase 3a roster omission: a listener that excludes the subject gets no
		 * join/leave dot for it — same set that culls its audio (single source of
		 * truth). The apply path emits the corrective join/leave on transitions. */
		if(slv_roster_excludes(p->excluded, who))
			continue;
		janus_slvoice_relay_json(p, entry);
	}
	janus_mutex_unlock(&room->mutex);
	json_decref(entry);
}

/* Join-backlog (docs/join-backlog-defect.md): when a listener's data channel
 * becomes writable, send it a synthetic "j" for every OTHER participant already
 * in its room, so an occupant who was present before this listener joined gets a
 * participant row immediately instead of only on the next incidental transition
 * (speech/movement/visibility-resync). push_presence tells the ROOM about a
 * newcomer; it never tells a newcomer about the room. This closes that gap for
 * the newcomer, at the earliest point relay_data is allowed (dc_open just set).
 *
 * Wire format is byte-identical to a live push_presence "j" ({who:{"j":{"p":true}}}),
 * so the viewer cannot distinguish a backlog join from a live one. A backlog "j"
 * that races a real transition "j" for the same source is a no-op at the viewer
 * (addParticipantByID -> addParticipant is idempotent by UUID; a second "j" only
 * re-notifies, never resets), so no mixer-side dedup is required.
 *
 * SECURITY: filtered by the LISTENER's own exclusion set via slv_roster_excludes
 * -- the SAME predicate push_presence (:614), the join-time roster (:1751) and the
 * tick dot-batch use. A source the listener's visibility column hides is omitted
 * here identically to how it is omitted from live joins and dots; the backlog can
 * reveal nothing the matrix has already hidden. Any exclusion-set lag (a batch not
 * yet applied at data_ready) is corrected by apply_visbatch exactly as it corrects
 * a live join (:1273) -- the backlog carries no exposure a live join does not.
 *
 * Locking: grab a room ref under session->mutex then release it before taking
 * room->mutex (order is room->mutex -> session->mutex), mirroring query_session
 * (:979-:991). Caller must NOT hold room->mutex. */
/* Does this listener currently moderation-mute the given source? Reads the listener's peer_ctl under
 * L->mutex (its discipline). Caller may hold room->mutex (order room->mutex -> session->mutex). */
static gboolean janus_slvoice_is_mod_muted(janus_slvoice_session *L, const char *uuid) {
	gboolean r = FALSE;
	janus_mutex_lock(&L->mutex);
	for(int k = 0; k < L->n_peer_ctl; k++) {
		if(strcmp(L->peer_ctl[k].uuid, uuid) == 0) { r = L->peer_ctl[k].mod_muted; break; }
	}
	janus_mutex_unlock(&L->mutex);
	return r;
}

/* M-A2A-1: the snapshot+send body, with room->mutex HELD by the caller (the data_ready
 * fence). Split out of janus_slvoice_send_join_backlog so the dc_open flip and this
 * snapshot are one critical section — see the invariant proof at the fence. */
static void janus_slvoice_send_join_backlog_locked(janus_slvoice_session *listener, janus_slvoice_room *room) {
	GHashTableIter iter;
	gpointer value;
	g_hash_table_iter_init(&iter, room->participants);
	while(g_hash_table_iter_next(&iter, NULL, &value)) {
		janus_slvoice_session *p = value;
		if(p == listener || p->display == NULL)
			continue;   /* skip self and unnamed; empty/alone -> nothing sent */
		if(slv_roster_excludes(listener->excluded, p->display))
			continue;   /* same visibility rule as live joins / dots (single source of truth) */
		json_t *entry = json_object();
		json_t *sub = json_object();
		json_t *jd = json_object();
		json_object_set_new(jd, "p", json_true());   /* {"j":{"p":true}} -- identical to push_presence */
		json_object_set_new(sub, "j", jd);
		/* #8 sticky-mute inheritance: if the joining listener already moderation-mutes this source,
		 * carry the flag IN the backlog "j" so the late joiner's row appears greyed from the start
		 * ({"j":{...},"m":true} — the viewer reads both from one object). If the listener's mute has
		 * not yet been applied at data_ready, the sim's per-listener mute replace greys it shortly
		 * after; the end state is the same. */
		if(janus_slvoice_is_mod_muted(listener, p->display))
			json_object_set_new(sub, "m", json_true());
		json_object_set_new(entry, p->display, sub);
		janus_slvoice_relay_json(listener, entry);   /* guards on listener->dc_open (now 1) */
		json_decref(entry);
	}
}

/* ---- Session helpers ----------------------------------------------------- */

static void janus_slvoice_session_free(const janus_refcount *ref) {
	janus_slvoice_session *session = janus_refcount_containerof(ref, janus_slvoice_session, ref);
	JANUS_LOG(LOG_VERB, "[%s] Freeing session %p\n", JANUS_SLVOICE_PACKAGE, session);
	janus_slvoice_media_free_locked(session);   /* belt-and-suspenders (already freed at leave) */
	if(session->excluded != NULL)
		g_hash_table_destroy(session->excluded);
	g_free(session->display);
	g_free(session);
}

static void janus_slvoice_session_destroy(janus_slvoice_session *session) {
	if(session && g_atomic_int_compare_and_exchange(&session->destroyed, 0, 1))
		janus_refcount_decrease(&session->ref);
}

static void janus_slvoice_message_free(janus_slvoice_message *msg) {
	if(!msg || msg == &exit_message)
		return;
	if(msg->handle && msg->handle->plugin_handle) {
		janus_slvoice_session *session = (janus_slvoice_session *)msg->handle->plugin_handle;
		janus_refcount_decrease(&session->ref);
	}
	msg->handle = NULL;
	g_free(msg->transaction);
	msg->transaction = NULL;
	if(msg->message)
		json_decref(msg->message);
	msg->message = NULL;
	if(msg->jsep)
		json_decref(msg->jsep);
	msg->jsep = NULL;
	g_free(msg);
}

/* ---- Static room loading (optional; rooms are primarily dynamic) ---------- */

static const char *janus_slvoice_cfg_item(janus_config *config, janus_config_category *cat, const char *name) {
	janus_config_item *it = janus_config_get(config, cat, janus_config_type_item, name);
	return (it && it->value) ? it->value : NULL;
}

static void janus_slvoice_load_static_rooms(janus_config *config) {
	if(config == NULL || rooms == NULL)
		return;
	GList *cats = janus_config_get_categories(config, NULL);
	GList *c = cats;
	while(c != NULL) {
		janus_config_category *cat = (janus_config_category *)c->data;
		c = c->next;
		if(cat->name == NULL || !strcasecmp(cat->name, "general"))
			continue;
		const char *room_s = janus_slvoice_cfg_item(config, cat, "room");
		if(room_s == NULL)
			continue;   /* not a room category */
		guint64 room_id = g_ascii_strtoull(room_s, NULL, 10);
		if(room_id == 0)
			continue;
		const char *desc = janus_slvoice_cfg_item(config, cat, "description");
		const char *rate_s = janus_slvoice_cfg_item(config, cat, "sampling_rate");
		const char *priv_s = janus_slvoice_cfg_item(config, cat, "is_private");
		const char *spatial_s = janus_slvoice_cfg_item(config, cat, "spatial_audio");
		guint32 rate = rate_s ? (guint32)g_ascii_strtoull(rate_s, NULL, 10) : 48000;
		gboolean is_private = priv_s && (!strcasecmp(priv_s, "true") || !strcasecmp(priv_s, "yes"));
		gboolean spatial = spatial_s && (!strcasecmp(spatial_s, "true") || !strcasecmp(spatial_s, "yes"));
		janus_mutex_lock(&rooms_mutex);
		if(g_hash_table_lookup(rooms, &room_id) == NULL) {
			janus_slvoice_room *room = janus_slvoice_room_create(room_id, desc, is_private, rate, spatial, TRUE);
			guint64 *key = g_malloc(sizeof(guint64));
			*key = room_id;
			g_hash_table_insert(rooms, key, room);
			JANUS_LOG(LOG_INFO, "[%s] Static room %"PRIu64" (%s) loaded from config\n",
				JANUS_SLVOICE_PACKAGE, room_id, room->description);
		}
		janus_mutex_unlock(&rooms_mutex);
	}
	g_list_free(cats);
}

/* Parse an optional [general] item as a C double. Returns FALSE if the key is
 * absent (the caller keeps the default). On a present-but-nonnumeric value it sets
 * *out to NAN and returns TRUE, so the validation below rejects the whole set. */
static gboolean janus_slvoice_cfg_double(janus_config *config, janus_config_category *cat,
		const char *name, double *out) {
	const char *s = janus_slvoice_cfg_item(config, cat, name);
	if(s == NULL)
		return FALSE;
	char *end = NULL;
	double v = g_ascii_strtod(s, &end);
	if(end == s || *end != '\0') {
		JANUS_LOG(LOG_ERR, "[%s] Config %s=\"%s\" is not a number\n", JANUS_SLVOICE_PACKAGE, name, s);
		*out = NAN;
		return TRUE;
	}
	*out = v;
	return TRUE;
}

/* Amendment 8: runtime spatial DSP config from the process-wide [general] jcfg
 * section. Distances are given in METRES and converted to stored units ONCE here.
 * The set is validated as a whole and applied all-or-nothing: any invalid value
 * logs an error and leaves the compiled defaults (already in slv_spatial) untouched,
 * so the tick never runs on nonsense. Absent keys keep the defaults, so an absent or
 * spatial-key-free config leaves behaviour byte-identical to the previous build. */
static void janus_slvoice_load_spatial_settings(janus_config *config) {
	if(config == NULL)
		return;
	janus_config_category *general =
		janus_config_get(config, NULL, janus_config_type_category, "general");
	if(general == NULL)
		return;

	/* Seed from the compiled metre defaults; override only the keys present. */
	double ref_m    = SLV_REF_DIST_M;
	double cutoff_m = SLV_CUTOFF_DIST_M;
	double readd_m  = SLV_READD_DIST_M;
	double leash_m  = SLV_LEASH_DIST_M;
	double falloff  = SLV_FALLOFF_EXP_DEFAULT;
	gboolean any = FALSE;
	any |= janus_slvoice_cfg_double(config, general, "spatial_reference_distance_m", &ref_m);
	any |= janus_slvoice_cfg_double(config, general, "spatial_cutoff_distance_m",    &cutoff_m);
	any |= janus_slvoice_cfg_double(config, general, "spatial_readd_distance_m",     &readd_m);
	any |= janus_slvoice_cfg_double(config, general, "spatial_leash_distance_m",     &leash_m);
	any |= janus_slvoice_cfg_double(config, general, "spatial_falloff_exp",          &falloff);
	if(!any)
		return;   /* no spatial keys present: defaults stand */

	/* Validate the whole set. Distances must be finite and > 0; ref < cutoff (the
	 * attenuation normaliser cutoff-ref must be > 0, else divide-by-zero / inversion);
	 * readd < cutoff (else the hysteresis band inverts); exponent > 0 (0 => no falloff,
	 * < 0 => +inf gain at the cutoff). */
	const char *bad = NULL;
	if(!(isfinite(ref_m)    && ref_m    > 0.0))      bad = "spatial_reference_distance_m must be > 0";
	else if(!(isfinite(cutoff_m) && cutoff_m > 0.0)) bad = "spatial_cutoff_distance_m must be > 0";
	else if(!(isfinite(readd_m)  && readd_m  > 0.0)) bad = "spatial_readd_distance_m must be > 0";
	else if(!(isfinite(leash_m)  && leash_m  > 0.0)) bad = "spatial_leash_distance_m must be > 0";
	else if(!(isfinite(falloff)  && falloff  > 0.0)) bad = "spatial_falloff_exp must be > 0";
	else if(!(ref_m   < cutoff_m))                   bad = "reference distance must be < cutoff (attenuation normaliser)";
	else if(!(readd_m < cutoff_m))                   bad = "re-add distance must be < cutoff (hysteresis band)";
	if(bad != NULL) {
		JANUS_LOG(LOG_ERR, "[%s] Invalid spatial config: %s; keeping compiled defaults\n",
			JANUS_SLVOICE_PACKAGE, bad);
		return;
	}

	/* Commit: metres -> stored units ONCE, here and nowhere else. */
	slv_spatial.ref_dist    = ref_m    * SLV_GEOM_SCALE;
	slv_spatial.cutoff_dist = cutoff_m * SLV_GEOM_SCALE;
	slv_spatial.readd_dist  = readd_m  * SLV_GEOM_SCALE;
	slv_spatial.leash_dist  = leash_m  * SLV_GEOM_SCALE;
	slv_spatial.falloff_exp = falloff;
	JANUS_LOG(LOG_INFO, "[%s] Spatial config applied: ref=%.1fm cutoff=%.1fm readd=%.1fm leash=%.1fm falloff_exp=%.2f\n",
		JANUS_SLVOICE_PACKAGE, ref_m, cutoff_m, readd_m, leash_m, falloff);
}

/* ---- Plugin lifecycle ---------------------------------------------------- */

int janus_slvoice_init(janus_callbacks *callback, const char *config_path) {
	if(g_atomic_int_get(&stopping))
		return -1;
	if(callback == NULL || config_path == NULL)
		return -1;

	char filename[255];
	g_snprintf(filename, sizeof(filename), "%s/%s.jcfg", config_path, JANUS_SLVOICE_PACKAGE);
	JANUS_LOG(LOG_VERB, "[%s] Configuration file: %s\n", JANUS_SLVOICE_PACKAGE, filename);
	janus_config *config = janus_config_parse(filename);
	if(config != NULL)
		janus_config_print(config);
	else
		JANUS_LOG(LOG_WARN, "[%s] No configuration file (%s) found, using defaults\n",
			JANUS_SLVOICE_PACKAGE, filename);

	sessions = g_hash_table_new(NULL, NULL);
	rooms = g_hash_table_new_full(g_int64_hash, g_int64_equal, g_free, NULL);
	messages = g_async_queue_new();

	/* Optional static rooms; dynamic create/join is the primary path */
	janus_slvoice_load_static_rooms(config);

	/* echo_autostart: jcfg general.echo_autostart, overridable by the
	 * SLV_ECHO_AUTOSTART environment variable (the convenient knob under the
	 * env-driven Docker model — just add it to .env). Stock viewers can't send
	 * the {"echo":true} SLData toggle, so this is how the operator turns echo on. */
	if(config != NULL) {
		janus_config_category *general = janus_config_get(config, NULL, janus_config_type_category, "general");
		const char *ea = general ? janus_slvoice_cfg_item(config, general, "echo_autostart") : NULL;
		if(ea && (!strcasecmp(ea, "true") || !strcasecmp(ea, "yes")))
			echo_autostart = TRUE;
	}
	const char *ea_env = getenv("SLV_ECHO_AUTOSTART");
	if(ea_env) {
		if(!strcasecmp(ea_env, "true") || !strcasecmp(ea_env, "1") || !strcasecmp(ea_env, "yes"))
			echo_autostart = TRUE;
		else if(!strcasecmp(ea_env, "false") || !strcasecmp(ea_env, "0") || !strcasecmp(ea_env, "no"))
			echo_autostart = FALSE;
	}
	if(echo_autostart)
		JANUS_LOG(LOG_INFO, "[%s] SLV_ECHO_AUTOSTART enabled — echo starts automatically on connect\n",
			JANUS_SLVOICE_PACKAGE);

	/* Amendment 8: runtime spatial DSP overrides from [general]; metres->stored
	 * converted once here, validated, defaults kept on any bad value. Must run
	 * BEFORE janus_config_destroy, which frees the parsed config. */
	janus_slvoice_load_spatial_settings(config);

	if(config != NULL)
		janus_config_destroy(config);

	gateway = callback;
	g_atomic_int_set(&initialized, 1);

	GError *error = NULL;
	handler_thread = g_thread_try_new("slvoice handler", janus_slvoice_handler, NULL, &error);
	if(error != NULL) {
		g_atomic_int_set(&initialized, 0);
		JANUS_LOG(LOG_ERR, "[%s] Got error %d (%s) launching the handler thread\n",
			JANUS_SLVOICE_PACKAGE, error->code, error->message ? error->message : "??");
		g_error_free(error);
		return -1;
	}
	error = NULL;
	sender_thread = g_thread_try_new("slvoice sender", janus_slvoice_sender, NULL, &error);
	if(error != NULL) {
		g_atomic_int_set(&initialized, 0);
		JANUS_LOG(LOG_ERR, "[%s] Got error %d (%s) launching the sender thread\n",
			JANUS_SLVOICE_PACKAGE, error->code, error->message ? error->message : "??");
		g_error_free(error);
		return -1;
	}

	JANUS_LOG(LOG_INFO, "%s initialized! (API v%d, %s)\n",
		JANUS_SLVOICE_NAME, JANUS_PLUGIN_API_VERSION, JANUS_SLVOICE_VERSION_STRING);
	return 0;
}

void janus_slvoice_destroy(void) {
	if(!g_atomic_int_get(&initialized))
		return;
	g_atomic_int_set(&stopping, 1);

	if(messages != NULL)
		g_async_queue_push(messages, &exit_message);
	if(handler_thread != NULL) {
		g_thread_join(handler_thread);
		handler_thread = NULL;
	}
	if(sender_thread != NULL) {
		g_thread_join(sender_thread);
		sender_thread = NULL;
	}

	janus_mutex_lock(&sessions_mutex);
	if(sessions != NULL) {
		g_hash_table_destroy(sessions);
		sessions = NULL;
	}
	janus_mutex_unlock(&sessions_mutex);

	janus_mutex_lock(&rooms_mutex);
	if(rooms != NULL) {
		GHashTableIter iter;
		gpointer value;
		g_hash_table_iter_init(&iter, rooms);
		while(g_hash_table_iter_next(&iter, NULL, &value)) {
			janus_slvoice_room *room = value;
			g_atomic_int_set(&room->destroyed, 1);
			janus_slvoice_room_stop(room);   /* join the tick thread */
			janus_refcount_decrease(&room->ref);
		}
		g_hash_table_destroy(rooms);
		rooms = NULL;
	}
	janus_mutex_unlock(&rooms_mutex);

	if(messages != NULL) {
		g_async_queue_unref(messages);
		messages = NULL;
	}

	gateway = NULL;
	g_atomic_int_set(&initialized, 0);
	g_atomic_int_set(&stopping, 0);
	JANUS_LOG(LOG_INFO, "%s destroyed!\n", JANUS_SLVOICE_NAME);
}

int janus_slvoice_get_api_compatibility(void) {
	/* Important! This is what your plugin MUST always return: don't lie here or bad things will happen */
	return JANUS_PLUGIN_API_VERSION;
}
int janus_slvoice_get_version(void) { return JANUS_SLVOICE_VERSION; }
const char *janus_slvoice_get_version_string(void) { return JANUS_SLVOICE_VERSION_STRING; }
const char *janus_slvoice_get_description(void) { return JANUS_SLVOICE_DESCRIPTION; }
const char *janus_slvoice_get_name(void) { return JANUS_SLVOICE_NAME; }
const char *janus_slvoice_get_author(void) { return JANUS_SLVOICE_AUTHOR; }
const char *janus_slvoice_get_package(void) { return JANUS_SLVOICE_PACKAGE; }

void janus_slvoice_create_session(janus_plugin_session *handle, int *error) {
	if(g_atomic_int_get(&stopping) || !g_atomic_int_get(&initialized)) {
		*error = -1;
		return;
	}
	janus_slvoice_session *session = g_malloc0(sizeof(janus_slvoice_session));
	session->handle = handle;
	session->opus_pt = -1;
	session->created_ts = janus_get_monotonic_time();
	session->excluded = g_hash_table_new_full(g_str_hash, g_str_equal, g_free, NULL);
	janus_mutex_init(&session->mutex);
	janus_refcount_init(&session->ref, janus_slvoice_session_free);
	handle->plugin_handle = session;

	janus_mutex_lock(&sessions_mutex);
	g_hash_table_insert(sessions, handle, session);
	janus_mutex_unlock(&sessions_mutex);

	JANUS_LOG(LOG_INFO, "[%s-%p] New session created\n", JANUS_SLVOICE_PACKAGE, handle);
}

void janus_slvoice_destroy_session(janus_plugin_session *handle, int *error) {
	if(g_atomic_int_get(&stopping) || !g_atomic_int_get(&initialized)) {
		*error = -1;
		return;
	}
	janus_slvoice_session *session = (janus_slvoice_session *)handle->plugin_handle;
	if(session == NULL) {
		JANUS_LOG(LOG_ERR, "[%s] No session associated with this handle...\n", JANUS_SLVOICE_PACKAGE);
		*error = -2;
		return;
	}
	JANUS_LOG(LOG_INFO, "[%s-%p] Destroying session (id=%"PRIu64")\n",
		JANUS_SLVOICE_PACKAGE, handle, session->user_id);
	/* Drop room membership FIRST (blocks on room->mutex if a tick is running, so
	 * the tick is done touching this participant), THEN free its media. */
	janus_slvoice_leave_room(session);
	janus_mutex_lock(&session->mutex);
	janus_slvoice_media_free_locked(session);
	janus_mutex_unlock(&session->mutex);

	janus_mutex_lock(&sessions_mutex);
	g_hash_table_remove(sessions, handle);
	janus_slvoice_session_destroy(session);
	janus_mutex_unlock(&sessions_mutex);
}

json_t *janus_slvoice_query_session(janus_plugin_session *handle) {
	if(g_atomic_int_get(&stopping) || !g_atomic_int_get(&initialized))
		return NULL;
	janus_slvoice_session *session = (janus_slvoice_session *)handle->plugin_handle;
	json_t *info = json_object();
	json_object_set_new(info, "plugin", json_string(JANUS_SLVOICE_PACKAGE));
	if(session == NULL) {
		json_object_set_new(info, "state", json_string("detached"));
		return info;
	}
	janus_mutex_lock(&session->mutex);
	gboolean up = g_atomic_int_get(&session->webrtc_up);
	/* Per-connection state vector (spec §4.1). Plugins only learn PeerConnection
	 * up/down (setup_media / hangup_media), so ICE/DTLS are reported coarsely. */
	json_object_set_new(info, "ice_state", json_string(up ? "connected" : "disconnected"));
	json_object_set_new(info, "dtls_state", json_string(up ? "connected" : "disconnected"));
	json_object_set_new(info, "datachannel_negotiated", session->has_datachannel ? json_true() : json_false());
	json_object_set_new(info, "datachannel_answered", session->dc_answered ? json_true() : json_false());
	json_object_set_new(info, "datachannel_open", g_atomic_int_get(&session->dc_open) ? json_true() : json_false());
	janus_slvoice_room *qroom = NULL;
	if(session->room != NULL && !g_atomic_int_get(&session->room->destroyed)) {
		qroom = session->room;
		janus_refcount_increase(&qroom->ref);
		json_object_set_new(info, "room", json_integer((json_int_t)qroom->room_id));
	}
	json_object_set_new(info, "id", json_integer((json_int_t)session->user_id));
	if(session->display)
		json_object_set_new(info, "display", json_string(session->display));
	json_object_set_new(info, "opus_pt", json_integer(session->opus_pt));
	/* Inbound / outbound liveness. */
	json_object_set_new(info, "rtp_in_count", json_integer((json_int_t)session->rtp_in_count));
	json_object_set_new(info, "rtp_out_count", json_integer((json_int_t)session->rtp_out_count));
	json_object_set_new(info, "rtp_in_rate", json_real(session->rtp_in_rate));
	json_object_set_new(info, "rtp_out_rate", json_real(session->rtp_out_rate));
	json_object_set_new(info, "decode_ok", session->decode_ok ? json_true() : json_false());
	json_object_set_new(info, "active", g_atomic_int_get(&session->active) ? json_true() : json_false());
	json_object_set_new(info, "frames_decoded", json_integer((json_int_t)session->frames_decoded));
	json_object_set_new(info, "frames_encoded", json_integer((json_int_t)session->frames_encoded));
	json_object_set_new(info, "frames_mixed", json_integer((json_int_t)session->frames_mixed));
	json_object_set_new(info, "echo_active", g_atomic_int_get(&session->echo_active) ? json_true() : json_false());
	json_object_set_new(info, "last_rms", json_real(session->last_rms));
	json_object_set_new(info, "last_mix_rms", json_real(session->last_mix_rms));   /* listener's mixed-OUTPUT level (vs last_rms = inbound talker input) */
	json_object_set_new(info, "last_mix_rms_l", json_real(session->last_mix_rms_l)); /* per-channel output level (Phase 3b item 4 pan diagnostic; L/R swap detector) */
	json_object_set_new(info, "last_mix_rms_r", json_real(session->last_mix_rms_r));
	json_object_set_new(info, "peer_ctl_entries", json_integer(session->n_peer_ctl));
	/* Deliverable 5: how many of this listener's peer_ctl entries are SIM moderation-muted
	 * (the mix silences a source when mod_muted OR the viewer's own `muted` is set). The
	 * moderation flag was previously invisible to the admin API. Read under session->mutex. */
	int mod_muted_count = 0;
	for(int mmk = 0; mmk < session->n_peer_ctl; mmk++)
		if(session->peer_ctl[mmk].mod_muted)
			mod_muted_count++;
	json_object_set_new(info, "mod_muted_entries", json_integer(mod_muted_count));
	json_object_set_new(info, "excluded_entries",
		json_integer(session->excluded ? (json_int_t)g_hash_table_size(session->excluded) : 0));
	json_object_set_new(info, "data_msgs_received", json_integer((json_int_t)session->data_msgs_received));
	/* M-A2A-1: presence delivery observability (this session as RECIPIENT). After the
	 * data_ready fence, presence_dropped_dc_closed counting up on a session whose
	 * channel later opened would indicate a NEW bug, not the (closed) join race. */
	json_object_set_new(info, "presence_pushed", json_integer((json_int_t)session->presence_pushed));
	json_object_set_new(info, "presence_dropped_dc_closed", json_integer((json_int_t)session->presence_dropped_dc_closed));
	char fbuf[96];
	slv_sldata_fields_str(session->last_data_fields, fbuf, sizeof(fbuf));
	json_object_set_new(info, "last_data_fields_seen", json_string(fbuf));
	gint64 uptime = (janus_get_monotonic_time() - session->created_ts) / G_USEC_PER_SEC;
	json_object_set_new(info, "session_uptime", json_integer((json_int_t)uptime));
	janus_mutex_unlock(&session->mutex);

	/* Room-scoped fields (mix memberships + tick histogram). Taken AFTER dropping
	 * session->mutex to preserve the room->mutex -> session->mutex lock order. */
	if(qroom != NULL) {
		int active_others = 0;
		janus_mutex_lock(&qroom->mutex);
		GHashTableIter it;
		gpointer v;
		g_hash_table_iter_init(&it, qroom->participants);
		while(g_hash_table_iter_next(&it, NULL, &v)) {
			janus_slvoice_session *p = (janus_slvoice_session *)v;
			if(p != session && g_atomic_int_get(&p->active))
				active_others++;
		}
		/* mix_memberships: for an active talker, the number of listeners its
		 * audio is folded into right now (every other participant); 0 if culled. */
		int memberships = g_atomic_int_get(&session->active)
			? (int)g_hash_table_size(qroom->participants) - 1 : 0;
		if(memberships < 0)
			memberships = 0;
		json_object_set_new(info, "mix_memberships", json_integer(memberships));
		json_object_set_new(info, "mix_sources", json_integer(active_others));
		/* Room-level occupancy vs the join-time cap. DISTINCT from mix_memberships /
		 * mix_sources above (those are per-session mix counts): room_participants is
		 * this room's total membership, room_capacity is SLV_MAX_MIX, the ceiling the
		 * join path rejects past (ROOM_FULL). Diagnostic only — lets a poller see how
		 * close a room is to full proactively, rather than learning it reactively when a
		 * provision is rejected. Reuses the g_hash_table_size already read under this
		 * same qroom->mutex hold; no new lock, no effect on admission/tick/audio. */
		json_object_set_new(info, "room_participants", json_integer((json_int_t)g_hash_table_size(qroom->participants)));
		json_object_set_new(info, "room_capacity", json_integer(SLV_MAX_MIX));
		json_t *hist = json_object();
		json_object_set_new(hist, "lt5ms", json_integer((json_int_t)qroom->tick_hist[0]));
		json_object_set_new(hist, "5to10ms", json_integer((json_int_t)qroom->tick_hist[1]));
		json_object_set_new(hist, "10to15ms", json_integer((json_int_t)qroom->tick_hist[2]));
		json_object_set_new(hist, "15to20ms", json_integer((json_int_t)qroom->tick_hist[3]));
		json_object_set_new(hist, "ge20ms", json_integer((json_int_t)qroom->tick_hist[4]));
		json_object_set_new(hist, "overruns", json_integer((json_int_t)qroom->tick_overruns));
		json_object_set_new(hist, "last_ms", json_real(qroom->tick_last_ms));
		json_object_set_new(hist, "max_ms", json_real(qroom->tick_max_ms));
		json_object_set_new(hist, "tick_seq", json_integer((json_int_t)qroom->tick_seq));
		/* Phase 3a visibility feed state (the admin-API verification surface).
		 * epoch = batches applied; joins_since_snapshot = roster-ahead-of-feed
		 * count (reset by each REPLACE snapshot). */
		json_t *vis = json_object();
		json_object_set_new(vis, "epoch", json_integer((json_int_t)qroom->vis_epoch));
		json_object_set_new(vis, "have_batch", qroom->vis_have_batch ? json_true() : json_false());
		if(qroom->vis_have_batch) {
			json_object_set_new(vis, "last_mode", json_string(slv_vis_op_str(qroom->vis_last_mode)));
			gint64 age_ms = (janus_get_monotonic_time() - qroom->vis_last_ts) / 1000;
			json_object_set_new(vis, "last_batch_age_ms", json_integer((json_int_t)age_ms));
		}
		json_object_set_new(vis, "joins_since_snapshot",
			json_integer((json_int_t)qroom->vis_joins_since_snapshot));
		/* ROOM-level dropped-listener instrument (§M follow-up): entries whose listener
		 * was not in the room. Distinct by name from the per-session excluded_entries. */
		json_object_set_new(vis, "dropped_listener_entries",
			json_integer((json_int_t)qroom->vis_dropped_listener_entries));
		json_object_set_new(vis, "last_batch_dropped_listeners",
			json_integer((json_int_t)qroom->vis_last_batch_dropped_listeners));
		/* Join-window fix (src/deferred.h): the deferred store's counters -- columns held for
		 * listeners not yet in the room, and their disposition (replayed on join / evicted). */
		json_object_set_new(vis, "deferred_current",
			json_integer((json_int_t)slv_deferred_count(&qroom->vis_deferred)));
		json_object_set_new(vis, "deferred_adds",
			json_integer((json_int_t)qroom->vis_deferred.adds));
		json_object_set_new(vis, "deferred_replaced",
			json_integer((json_int_t)qroom->vis_deferred.replaced));
		json_object_set_new(vis, "deferred_replayed",
			json_integer((json_int_t)qroom->vis_deferred.replayed));
		json_object_set_new(vis, "deferred_evicted",
			json_integer((json_int_t)qroom->vis_deferred.evicted));
		janus_mutex_unlock(&qroom->mutex);
		json_object_set_new(info, "tick_histogram", hist);
		json_object_set_new(info, "visibility", vis);
		janus_refcount_decrease(&qroom->ref);
	}
	return info;
}

/* ---- handle_message: validate + enqueue (async) -------------------------- */

struct janus_plugin_result *janus_slvoice_handle_message(janus_plugin_session *handle,
		char *transaction, json_t *message, json_t *jsep) {
	if(g_atomic_int_get(&stopping) || !g_atomic_int_get(&initialized)) {
		g_free(transaction);
		if(message) json_decref(message);
		if(jsep) json_decref(jsep);
		return janus_plugin_result_new(JANUS_PLUGIN_ERROR, "Shutting down", NULL);
	}
	janus_slvoice_session *session = (janus_slvoice_session *)handle->plugin_handle;
	if(session == NULL || g_atomic_int_get(&session->destroyed)) {
		g_free(transaction);
		if(message) json_decref(message);
		if(jsep) json_decref(jsep);
		return janus_plugin_result_new(JANUS_PLUGIN_ERROR, "No session associated with this handle", NULL);
	}
	if(message == NULL || !json_is_object(message)) {
		g_free(transaction);
		if(message) json_decref(message);
		if(jsep) json_decref(jsep);
		return janus_plugin_result_new(JANUS_PLUGIN_ERROR, "Invalid message (not an object)", NULL);
	}
	/* Hand off to the async handler; it owns transaction/message/jsep now. */
	janus_refcount_increase(&session->ref);
	janus_slvoice_message *msg = g_malloc(sizeof(janus_slvoice_message));
	msg->handle = handle;
	msg->transaction = transaction;
	msg->message = message;
	msg->jsep = jsep;
	g_async_queue_push(messages, msg);
	return janus_plugin_result_new(JANUS_PLUGIN_OK_WAIT, NULL, NULL);
}

/* Build {who:{"j":{"p":true}}} (join) or {who:{"l":true}} (leave) and relay it to
 * one listener — the per-(listener,source) transition emit used when a visibility
 * change flips an already-present pair. Caller must NOT hold L->mutex (relay needs
 * no session lock; matches janus_slvoice_push_presence). */
static void janus_slvoice_relay_presence_one(janus_slvoice_session *L, const char *who, gboolean join) {
	json_t *entry = json_object();
	json_t *sub = json_object();
	if(join) {
		json_t *jd = json_object();
		json_object_set_new(jd, "p", json_true());
		json_object_set_new(sub, "j", jd);
	} else {
		json_object_set_new(sub, "l", json_true());
	}
	json_object_set_new(entry, who, sub);
	janus_slvoice_relay_json(L, entry);
	json_decref(entry);
}

/* Apply one parsed visibility batch (peer_ctl_batch, §3.3) to its room.
 *
 * ATOMICITY — SHARED LOCK (explicitly NOT a staged double-buffer + tick-boundary
 * swap): the whole batch is applied while holding room->mutex, the SAME lock
 * janus_slvoice_room_tick holds for its ENTIRE pass. So a batch lands wholly
 * between two mix ticks and is never observed half-applied across a pass. A staged
 * buffer + swap was considered and rejected: at one batch per ~250ms feeder tick
 * vs 20ms mix ticks the lock is held sub-millisecond and contention is negligible,
 * and the shared lock keeps every reader — tick, sender, presence — on one
 * discipline (room->mutex -> session->mutex). Per-listener mutation also takes
 * L->mutex so query_session (session->mutex) reads a consistent excluded size.
 *
 * Returns TRUE if the batch was applied, FALSE if vb->room named no room in this
 * process — the caller turns that into an unknown_room error instead of reporting
 * success for a batch that landed nowhere. */
static gboolean janus_slvoice_apply_visbatch(const slv_visbatch *vb, guint64 *out_deferred) {
	janus_slvoice_room *room = janus_slvoice_room_ref_by_id((guint64)vb->room);
	if(room == NULL) {
		JANUS_LOG(LOG_WARN, "[%s] peer_ctl_batch for unknown room %"PRId64" (op=%s); dropped\n",
			JANUS_SLVOICE_PACKAGE, (int64_t)vb->room, slv_vis_op_str(vb->op));
		if(out_deferred) *out_deferred = 0;
		return FALSE;
	}
	janus_mutex_lock(&room->mutex);

	guint64 dropped_this_batch = 0;   /* listener entries skipped (nmatch==0) in THIS batch */

	/* Index the room once: display -> session (listener lookup) doubling as the
	 * present-set for transition gating (emit join/leave only for present sources). */
	GHashTable *by_display = g_hash_table_new(g_str_hash, g_str_equal);
	GHashTableIter it;
	gpointer v;
	g_hash_table_iter_init(&it, room->participants);
	while(g_hash_table_iter_next(&it, NULL, &v)) {
		janus_slvoice_session *p = v;
		if(p->display != NULL)
			g_hash_table_insert(by_display, p->display, p);   /* borrowed key + value */
	}

	for(int i = 0; i < vb->n_entries; i++) {
		const slv_vis_entry *e = &vb->entries[i];
		/* Fan-out (Amendment 7): an exclusion entry names an AVATAR — the listener's
		 * agent UUID (= display), the only identifier the feed carries (visbatch.h) —
		 * not a specific handle. It therefore applies to EVERY session in the room
		 * whose display matches, not to whichever one a single-value map happens to
		 * keep (that would depend on undefined GLib iteration order; §M). Scan
		 * room->participants; each match merges into ITS OWN excluded table. by_display
		 * is retained only as the O(1) present-set for the source-side transition gate
		 * below — a key-membership test, unaffected by listener duplicates. */
		int nmatch = 0;
		guint64 match_ids[SLV_MAX_MIX];
		GHashTableIter lit;
		gpointer lv;
		g_hash_table_iter_init(&lit, room->participants);
		while(g_hash_table_iter_next(&lit, NULL, &lv)) {
			janus_slvoice_session *L = lv;
			if(L->display == NULL || strcmp(L->display, e->listener) != 0)
				continue;   /* not this listener avatar */
			if(nmatch < SLV_MAX_MIX)
				match_ids[nmatch] = L->user_id;
			nmatch++;

			/* Transitions to emit AFTER releasing L->mutex. Bounded by set sizes; an
			 * overflow only delays a dot flip by one sender tick (~100ms backstop). */
			char trans_uuid[SLV_VIS_MAX_EXCL * 2][SLV_UUID_LEN];
			gboolean trans_join[SLV_VIS_MAX_EXCL * 2];
			int ntrans = 0;

			/* Each matching session merges into its OWN L->excluded — never a shared
			 * table pointer. L->excluded is destroyed per session at teardown (:570)
			 * and destroyed-and-replaced wholesale by the REPLACE branch below
			 * (:1078-:1079), so sharing one pointer across sessions would double-free. */
			janus_mutex_lock(&L->mutex);
			if(vb->op == SLV_VIS_OP_ADD) {
				for(int k = 0; k < e->n_excl; k++) {
					const char *s = e->excl[k];
					if(!g_hash_table_contains(L->excluded, s)) {
						g_hash_table_add(L->excluded, g_strdup(s));
						if(ntrans < SLV_VIS_MAX_EXCL * 2 && g_hash_table_contains(by_display, s)) {
							g_strlcpy(trans_uuid[ntrans], s, SLV_UUID_LEN);
							trans_join[ntrans++] = FALSE;   /* newly excluded -> leave */
						}
					}
				}
			} else if(vb->op == SLV_VIS_OP_REMOVE) {
				for(int k = 0; k < e->n_excl; k++) {
					const char *s = e->excl[k];
					if(g_hash_table_remove(L->excluded, s)) {
						if(ntrans < SLV_VIS_MAX_EXCL * 2 && g_hash_table_contains(by_display, s)) {
							g_strlcpy(trans_uuid[ntrans], s, SLV_UUID_LEN);
							trans_join[ntrans++] = TRUE;   /* un-excluded -> join */
						}
					}
				}
			} else {   /* SLV_VIS_OP_REPLACE: set exactly; diff old vs new for transitions */
				GHashTable *newset = g_hash_table_new_full(g_str_hash, g_str_equal, g_free, NULL);
				for(int k = 0; k < e->n_excl; k++)
					g_hash_table_add(newset, g_strdup(e->excl[k]));
				/* removed = old \ new -> join (if present) */
				GHashTableIter oit;
				gpointer ok;
				g_hash_table_iter_init(&oit, L->excluded);
				while(g_hash_table_iter_next(&oit, &ok, NULL)) {
					const char *s = ok;
					if(!g_hash_table_contains(newset, s)
							&& ntrans < SLV_VIS_MAX_EXCL * 2 && g_hash_table_contains(by_display, s)) {
						g_strlcpy(trans_uuid[ntrans], s, SLV_UUID_LEN);
						trans_join[ntrans++] = TRUE;
					}
				}
				/* added = new \ old -> leave (if present) */
				for(int k = 0; k < e->n_excl; k++) {
					const char *s = e->excl[k];
					if(!g_hash_table_contains(L->excluded, s)
							&& ntrans < SLV_VIS_MAX_EXCL * 2 && g_hash_table_contains(by_display, s)) {
						g_strlcpy(trans_uuid[ntrans], s, SLV_UUID_LEN);
						trans_join[ntrans++] = FALSE;
					}
				}
				g_hash_table_destroy(L->excluded);
				L->excluded = newset;
			}
			janus_mutex_unlock(&L->mutex);

			/* Presence transitions are keyed by the SOURCE display (agent UUID), so a
			 * duplicated source still emits ONE dot per avatar (the gate above is a
			 * by_display membership test). But each matching LISTENER session is a
			 * distinct WebRTC connection and must be told separately, so we emit here,
			 * inside the per-session loop — once per (matching listener, source avatar). */
			for(int t = 0; t < ntrans; t++)
				janus_slvoice_relay_presence_one(L, trans_uuid[t], trans_join[t]);
		}

		/* nmatch == 0 means this listener avatar is not in the room right now — the
		 * scan applied nothing and the entry is skipped (this is the old L==NULL skip,
		 * now implicit). COUNTED, not silent: a correct drop (listener just left, the
		 * ~250ms feeder tick plus sender latency makes that routine around departures)
		 * and a wrong one look identical without an instrument. LOG_VERB per drop only:
		 * normal churn fires this a few times per departure, so a WARN would flood
		 * during events; the counters carry the operational signal. */
		if(nmatch == 0) {
			room->vis_dropped_listener_entries++;
			dropped_this_batch++;
			/* Join-window fix: rather than lose it, DEFER this listener's exclusion column so
			 * the join branch replays it when the listener finally joins the room. */
			slv_deferred_put(&room->vis_deferred, e->listener, vb->op, e->excl, e->n_excl, 0);
			JANUS_LOG(LOG_VERB, "[%s] peer_ctl_batch listener %s (op=%s, room %"PRId64") "
				"not in room; entry dropped (dropped_listener_entries=%"PRIu64")\n",
				JANUS_SLVOICE_PACKAGE, e->listener, slv_vis_op_str(vb->op),
				(int64_t)vb->room, room->vis_dropped_listener_entries);
		}
		if(nmatch > 1) {
			/* Amendment 7 / §M: this resolution was silent before — every counter read
			 * healthy while one handle got the exclusion and the other silently did not.
			 * Name the display and the colliding user_ids now that we apply to all. The
			 * merge above already fanned out to ALL nmatch sessions; match_ids only holds
			 * the first SLV_MAX_MIX for this log line (room->participants is not capped at
			 * join, so nmatch > cap, while pathological, is not impossible), so the id
			 * list is flagged truncated when that bound is exceeded. */
			char ids[SLV_MAX_MIX * 24];   /* 24 > max digits(guint64) 20 + comma; holds all cap ids */
			int off = 0;
			for(int m = 0; m < nmatch && m < SLV_MAX_MIX; m++)
				off += g_snprintf(ids + off, sizeof(ids) - (gsize)off, "%s%"PRIu64,
					m ? "," : "", match_ids[m]);
			JANUS_LOG(LOG_WARN, "[%s] peer_ctl_batch listener %s (op=%s, room %"PRId64") "
				"matched %d sessions (user_ids: %s%s) — duplicate display; applied to all "
				"(parcel-voice-semantics.md §M)\n",
				JANUS_SLVOICE_PACKAGE, e->listener, slv_vis_op_str(vb->op),
				(int64_t)vb->room, nmatch, ids,
				nmatch > SLV_MAX_MIX ? " (user_ids truncated)" : "");
		}
	}

	g_hash_table_destroy(by_display);

	room->vis_epoch++;
	room->vis_last_ts = janus_get_monotonic_time();
	room->vis_last_mode = vb->op;
	room->vis_have_batch = TRUE;
	room->vis_last_batch_dropped_listeners = dropped_this_batch;
	if(out_deferred) *out_deferred = dropped_this_batch;   /* == deferred this batch (each drop is now a defer) */
	if(vb->op == SLV_VIS_OP_REPLACE)
		room->vis_joins_since_snapshot = 0;   /* a fresh snapshot re-derives the roster */

	janus_mutex_unlock(&room->mutex);
	janus_refcount_decrease(&room->ref);
	return TRUE;
}

/* Build {who:{"m":true|false}} and relay it to one listener — the moderation-mute transition emit
 * (Option A). Same field the viewer reads into mIsModeratorMuted: it KEEPS the row and greys it,
 * never removes it (contrast the "l" leave). Caller must NOT hold L->mutex. */
static void janus_slvoice_relay_mute_one(janus_slvoice_session *L, const char *who, gboolean muted) {
	json_t *entry = json_object();
	json_t *sub = json_object();
	json_object_set_new(sub, "m", muted ? json_true() : json_false());
	json_object_set_new(entry, who, sub);
	janus_slvoice_relay_json(L, entry);
	json_decref(entry);
}

/* Set/clear this listener's MODERATION mute for one source in its persistent peer_ctl map, creating
 * the entry if needed. Returns TRUE iff mod_muted actually CHANGED (so the caller emits an "m"
 * transition). L->mutex held. Never touches `muted` (the viewer's personal mute) or `gain`, so the
 * two mute sources compose (the mix silences on either) and clearing one never wipes the other. */
static gboolean janus_slvoice_set_mod_muted_locked(janus_slvoice_session *L, const char *uuid, gboolean muted) {
	slv_peer_ctl *dst = NULL;
	for(int k = 0; k < L->n_peer_ctl; k++) {
		if(strcmp(L->peer_ctl[k].uuid, uuid) == 0) { dst = &L->peer_ctl[k]; break; }
	}
	if(dst == NULL) {
		if(!muted)
			return FALSE;   /* clearing a mute that was never set: no entry, no change */
		if(L->n_peer_ctl >= SLV_MAX_PEER_ADJ)
			return FALSE;   /* peer_ctl table full: drop (source stays audible; bounded) */
		dst = &L->peer_ctl[L->n_peer_ctl++];
		memset(dst, 0, sizeof(*dst));
		g_strlcpy(dst->uuid, uuid, SLV_UUID_LEN);
	}
	if((dst->mod_muted ? TRUE : FALSE) == (muted ? TRUE : FALSE))
		return FALSE;
	dst->mod_muted = muted ? TRUE : FALSE;
	return TRUE;
}

/* Apply the parsed MODERATION MUTE channel (Option A) to its room. PARALLEL to apply_visbatch and
 * deliberately separate from it: a mute NEVER adds to L->excluded and NEVER synthesises a leave, so
 * the source stays in the roster (push_presence / the dot batch / the backlog all key on L->excluded,
 * which is untouched here). It sets the listener's per-source mod_muted (the mix silences it) and
 * emits {source:{"m":bool}} on transitions so the viewer greys / un-greys the row. Ban precedence is
 * upstream: VisibilityMatrix.Build never puts a source in BOTH channels (ban wins), so a removed
 * source is not also here. Returns TRUE if applied, FALSE if the room is unknown (mirrors
 * apply_visbatch so the caller reports unknown_room). No-op (TRUE) when there are no mute entries. */
static gboolean janus_slvoice_apply_mutebatch(const slv_visbatch *vb, guint64 *out_deferred) {
	if(vb->n_mute_entries == 0) {
		if(out_deferred) *out_deferred = 0;
		return TRUE;
	}
	janus_slvoice_room *room = janus_slvoice_room_ref_by_id((guint64)vb->room);
	if(room == NULL) {
		JANUS_LOG(LOG_WARN, "[%s] peer_ctl_batch mute for unknown room %"PRId64" (op=%s); dropped\n",
			JANUS_SLVOICE_PACKAGE, (int64_t)vb->room, slv_vis_op_str(vb->op));
		if(out_deferred) *out_deferred = 0;
		return FALSE;
	}
	janus_mutex_lock(&room->mutex);
	guint64 deferred_this_batch = 0;
	for(int i = 0; i < vb->n_mute_entries; i++) {
		const slv_vis_entry *e = &vb->mute_entries[i];
		GHashTableIter lit;
		gpointer lv;
		int nmatch = 0;
		g_hash_table_iter_init(&lit, room->participants);
		while(g_hash_table_iter_next(&lit, NULL, &lv)) {
			janus_slvoice_session *L = lv;
			if(L->display == NULL || strcmp(L->display, e->listener) != 0)
				continue;   /* fan-out to EVERY session matching the listener display (§M) */
			nmatch++;

			char trans_uuid[SLV_VIS_MAX_EXCL * 2][SLV_UUID_LEN];
			gboolean trans_mute[SLV_VIS_MAX_EXCL * 2];
			int ntrans = 0;

			janus_mutex_lock(&L->mutex);
			if(vb->op == SLV_VIS_OP_REPLACE) {
				/* Set the listener's mod-mute set EXACTLY: clear any currently-muted source not in
				 * the new set, then set every source in the new set. */
				for(int k = 0; k < L->n_peer_ctl; k++) {
					if(!L->peer_ctl[k].mod_muted)
						continue;
					gboolean in_new = FALSE;
					for(int m = 0; m < e->n_excl; m++)
						if(strcmp(L->peer_ctl[k].uuid, e->excl[m]) == 0) { in_new = TRUE; break; }
					if(!in_new) {
						L->peer_ctl[k].mod_muted = FALSE;
						if(ntrans < SLV_VIS_MAX_EXCL * 2) {
							g_strlcpy(trans_uuid[ntrans], L->peer_ctl[k].uuid, SLV_UUID_LEN);
							trans_mute[ntrans++] = FALSE;
						}
					}
				}
				for(int m = 0; m < e->n_excl; m++) {
					if(janus_slvoice_set_mod_muted_locked(L, e->excl[m], TRUE) && ntrans < SLV_VIS_MAX_EXCL * 2) {
						g_strlcpy(trans_uuid[ntrans], e->excl[m], SLV_UUID_LEN);
						trans_mute[ntrans++] = TRUE;
					}
				}
			} else {
				gboolean want = (vb->op == SLV_VIS_OP_ADD) ? TRUE : FALSE;
				for(int m = 0; m < e->n_excl; m++) {
					if(janus_slvoice_set_mod_muted_locked(L, e->excl[m], want) && ntrans < SLV_VIS_MAX_EXCL * 2) {
						g_strlcpy(trans_uuid[ntrans], e->excl[m], SLV_UUID_LEN);
						trans_mute[ntrans++] = want;
					}
				}
			}
			janus_mutex_unlock(&L->mutex);

			for(int t = 0; t < ntrans; t++)
				janus_slvoice_relay_mute_one(L, trans_uuid[t], trans_mute[t]);
		}
		if(nmatch == 0) {
			/* Join-window fix: no session for this listener yet -- DEFER its MUTE column so the
			 * join branch replays it (greyed row from the start) when it joins. */
			slv_deferred_put(&room->vis_deferred, e->listener, vb->op, e->excl, e->n_excl, 1);
			deferred_this_batch++;
		}
	}
	if(out_deferred) *out_deferred = deferred_this_batch;
	janus_mutex_unlock(&room->mutex);
	janus_refcount_decrease(&room->ref);
	return TRUE;
}

/* Replay any columns DEFERRED for `L` (its exclusion/mute set arrived before it joined) into its
 * freshly-created session state, then remove the deferred record. Called from the join branch with
 * room->mutex HELD and BEFORE the join roster is built / push_presence / the data_ready backlog, so
 * the listener's exclusions and mod-mutes are in force before any presence derived from them is
 * revealed. Takes L->mutex (order room->mutex -> session->mutex) to mutate L->excluded / peer_ctl. */
static void janus_slvoice_room_replay_deferred_locked(janus_slvoice_room *room, janus_slvoice_session *L) {
	if(L == NULL || L->display == NULL)
		return;
	const slv_deferred_entry *d = slv_deferred_get(&room->vis_deferred, L->display);
	if(d == NULL)
		return;
	int ne = d->n_excl, nm = d->n_mute;   /* capture for the log before replay_done frees d */
	janus_mutex_lock(&L->mutex);
	for(int i = 0; i < d->n_excl; i++)
		if(L->excluded != NULL && !g_hash_table_contains(L->excluded, d->excl[i]))
			g_hash_table_add(L->excluded, g_strdup(d->excl[i]));
	for(int i = 0; i < d->n_mute; i++)
		janus_slvoice_set_mod_muted_locked(L, d->mute[i], TRUE);   /* greying rides the backlog "m" */
	janus_mutex_unlock(&L->mutex);
	slv_deferred_replay_done(&room->vis_deferred, L->display);   /* frees d; remove after successful replay */
	JANUS_LOG(LOG_VERB, "[%s] replayed deferred columns for listener %s (excl=%d, mute=%d) on join to room %"PRIu64"\n",
		JANUS_SLVOICE_PACKAGE, L->display, ne, nm, room->room_id);
}

json_t *janus_slvoice_handle_admin_message(json_t *message) {
	const char *request_text = NULL;
	if(message != NULL && json_is_object(message)) {
		json_t *request = json_object_get(message, "request");
		if(json_is_string(request))
			request_text = json_string_value(request);
	}
	json_t *response = json_object();

	if(request_text != NULL && !strcmp(request_text, "peer_ctl_batch")) {
		/* Server-to-server visibility feed. Parse via the dependency-free visbatch
		 * module (unit-tested), then apply atomically. Malformed/unknown per-listener
		 * or per-source items were already skipped-and-counted by the parser; a batch
		 * missing op/room is rejected whole. Never affects a viewer session. */
		char *buf = json_dumps(message, JSON_COMPACT);
		slv_visbatch vb;
		slv_visbatch_status st = slv_visbatch_parse(buf, buf ? strlen(buf) : 0, &vb);
		if(buf != NULL)
			free(buf);
		if(st == SLV_VISBATCH_OK) {
			/* Apply the EXCLUSION channel first (ban/visibility -> leaves/removals), then the
			 * ADDITIVE moderation MUTE channel (keeps the row, silences, greys). Same room and same
			 * atomic contract; the two are independent per source (Build guarantees ban wins, so no
			 * source is in both). Each is skipped when its channel is empty; a channel with entries
			 * against an unknown room reports unknown_room, exactly as before. */
			guint64 excl_deferred = 0, mute_deferred = 0;
			gboolean exclOk = (vb.n_entries > 0) ? janus_slvoice_apply_visbatch(&vb, &excl_deferred) : TRUE;
			gboolean muteOk = (vb.n_mute_entries > 0) ? janus_slvoice_apply_mutebatch(&vb, &mute_deferred) : TRUE;
			if(exclOk && muteOk) {
				json_object_set_new(response, "slvoice", json_string("applied"));
				json_object_set_new(response, "op", json_string(slv_vis_op_str(vb.op)));
				json_object_set_new(response, "room", json_integer((json_int_t)vb.room));
				json_object_set_new(response, "entries", json_integer(vb.n_entries));
				json_object_set_new(response, "mute_entries", json_integer(vb.n_mute_entries));
				json_object_set_new(response, "skipped", json_integer(vb.n_skipped));
				/* ADDITIVE (join-window fix): listener entries DEFERRED this batch because the listener
				 * has not joined yet -- the count a sim reads to know its entry was retained, not
				 * silently dropped. An old sim ignores this extra key (skew-safe). */
				json_object_set_new(response, "deferred_listeners",
					json_integer((json_int_t)(excl_deferred + mute_deferred)));
			} else {
				/* Parsed fine but named no room in this process, so nothing was applied.
				 * Same {slvoice:error, reason} shape as the parse failures below, so a
				 * sender has one thing to test. apply_visbatch already logged the WARN. */
				json_object_set_new(response, "slvoice", json_string("error"));
				json_object_set_new(response, "reason", json_string("unknown_room"));
			}
		} else if(st == SLV_VISBATCH_EMPTY) {
			json_object_set_new(response, "slvoice", json_string("empty"));
			json_object_set_new(response, "skipped", json_integer(vb.n_skipped));
		} else {
			JANUS_LOG(LOG_WARN, "[%s] peer_ctl_batch rejected: %s\n", JANUS_SLVOICE_PACKAGE,
				st == SLV_VISBATCH_TOOBIG ? "too_big" : "malformed");
			json_object_set_new(response, "slvoice", json_string("error"));
			json_object_set_new(response, "reason",
				json_string(st == SLV_VISBATCH_TOOBIG ? "too_big" : "malformed"));
		}
		slv_visbatch_free(&vb);
		return response;
	}

	JANUS_LOG(LOG_WARN, "[%s] handle_admin_message: unknown request \"%s\"; rejected\n",
		JANUS_SLVOICE_PACKAGE, request_text ? request_text : "(none)");
	json_object_set_new(response, "slvoice", json_string("error"));
	json_object_set_new(response, "reason", json_string("unknown_request"));
	return response;
}

/* ---- JSEP negotiation helper (join / configure with an offer) ------------
 * Parses the offer, picks the Opus PT, and builds an answer that accepts BOTH
 * the m=audio (Opus 48k + spec §9 fmtp) AND the m=application (SCTP DataChannel)
 * lines. Answering the application line (JANUS_SDP_OA_MLINE, JANUS_SDP_APPLICATION,
 * modelled on janus_videoroom.c:13113) is THE fix for "Skipping unsupported
 * application media line" — see the file header. Returns TRUE + *answer_sdp
 * (caller g_free's). */
static gboolean janus_slvoice_negotiate(janus_slvoice_session *session, json_t *jsep,
		char **answer_sdp, int *error_code, char *error_cause) {
	*answer_sdp = NULL;
	const char *sdp_type = json_string_value(json_object_get(jsep, "type"));
	const char *sdp_str = json_string_value(json_object_get(jsep, "sdp"));
	if(sdp_type == NULL || sdp_str == NULL || strcasecmp(sdp_type, "offer") != 0) {
		*error_code = JANUS_SLVOICE_ERROR_INVALID_SDP;
		g_snprintf(error_cause, 512, "Expected a JSEP offer");
		return FALSE;
	}
	char err[512];
	janus_sdp *offer = janus_sdp_parse(sdp_str, err, sizeof(err));
	if(offer == NULL) {
		*error_code = JANUS_SLVOICE_ERROR_INVALID_SDP;
		g_snprintf(error_cause, 512, "Could not parse offer SDP: %s", err);
		return FALSE;
	}
	int opus_pt = janus_sdp_get_codec_pt(offer, -1, "opus");
	if(opus_pt <= 0) {
		janus_sdp_destroy(offer);
		*error_code = JANUS_SLVOICE_ERROR_INVALID_SDP;
		g_snprintf(error_cause, 512, "Offer does not include Opus");
		return FALSE;
	}
	gboolean has_dc = (janus_sdp_mline_find(offer, JANUS_SDP_APPLICATION) != NULL);
	JANUS_LOG(LOG_INFO, "[%s-%p] Offer received: Opus pt=%d, m=application present=%s\n",
		JANUS_SLVOICE_PACKAGE, session->handle, opus_pt, has_dc ? "yes" : "no");
	/* Full offer dump for capture against a live viewer (per-join, not spammy). */
	JANUS_LOG(LOG_INFO, "[%s-%p] ==== OFFER SDP ====\n%s==== END OFFER SDP ====\n",
		JANUS_SLVOICE_PACKAGE, session->handle, sdp_str);

	/* Build the answer: accept Opus audio sendrecv AND the datachannel; reject
	 * everything else (generate_answer defaults every m-line to rejected). */
	janus_sdp *answer = janus_sdp_generate_answer(offer);
	GList *temp = offer->m_lines;
	while(temp != NULL) {
		janus_sdp_mline *m = (janus_sdp_mline *)temp->data;
		if(m->type == JANUS_SDP_AUDIO) {
			/* Accept the mid + audio-level RTP header extensions the viewer
			 * offered. The mid extension is REQUIRED whenever the answer bundles
			 * more than one m-line (audio + data): without it a strict libwebrtc
			 * viewer (Firestorm) rejects the answer at setRemoteDescription and
			 * abandons before ICE even starts. Mirrors janus_audiobridge.c:8244. */
			janus_sdp_generate_answer_mline(offer, answer, m,
				JANUS_SDP_OA_MLINE, JANUS_SDP_AUDIO,
				JANUS_SDP_OA_CODEC, "opus",
				JANUS_SDP_OA_DIRECTION, JANUS_SDP_SENDRECV,
				JANUS_SDP_OA_ACCEPT_EXTMAP, JANUS_RTP_EXTMAP_MID,
				JANUS_SDP_OA_ACCEPT_EXTMAP, JANUS_RTP_EXTMAP_AUDIO_LEVEL,
				JANUS_SDP_OA_DONE);
		} else if(m->type == JANUS_SDP_APPLICATION) {
			/* Accept + answer the SCTP DataChannel so the core sets up SCTP
			 * (sdp.c:1611) instead of skipping it (sdp.c:1625); also accept the
			 * mid extension so the bundled data m-line carries its a=mid,
			 * matching the audio line. Mirrors janus_videoroom.c:13114. */
			janus_sdp_generate_answer_mline(offer, answer, m,
				JANUS_SDP_OA_MLINE, JANUS_SDP_APPLICATION,
				JANUS_SDP_OA_ACCEPT_EXTMAP, JANUS_RTP_EXTMAP_MID,
				JANUS_SDP_OA_DONE);
		}
		temp = temp->next;
	}
	/* Attach the SL WebRTC voice Opus fmtp (spec §9) as a raw attribute, the
	 * way audiobridge does: "<pt> <params>\r\n". */
	janus_sdp_mline *am = janus_sdp_mline_find(answer, JANUS_SDP_AUDIO);
	if(am != NULL) {
		char fmtp[160];
		g_snprintf(fmtp, sizeof(fmtp),
			"%d minptime=10;useinbandfec=1;stereo=1;sprop-stereo=1;maxplaybackrate=48000\r\n", opus_pt);
		janus_sdp_attribute *a = janus_sdp_attribute_create("fmtp", "%s", fmtp);
		janus_sdp_attribute_add_to_mline(am, a);
	}
	/* Did we actually accept the application m-line in the answer? (port>0) */
	janus_sdp_mline *am_dc = janus_sdp_mline_find(answer, JANUS_SDP_APPLICATION);
	gboolean dc_answered = (am_dc != NULL && am_dc->port > 0);

	char *new_sdp = janus_sdp_write(answer);
	janus_sdp_destroy(offer);
	janus_sdp_destroy(answer);
	if(new_sdp == NULL) {
		*error_code = JANUS_SLVOICE_ERROR_INVALID_SDP;
		g_snprintf(error_cause, 512, "Failed to write answer SDP");
		return FALSE;
	}
	janus_mutex_lock(&session->mutex);
	session->opus_pt = opus_pt;
	session->has_datachannel = has_dc;
	session->dc_answered = dc_answered;
	janus_mutex_unlock(&session->mutex);
	*answer_sdp = new_sdp;
	JANUS_LOG(LOG_INFO, "[%s-%p] Answer sent: audio Opus pt=%d; m=application answered=%s\n",
		JANUS_SLVOICE_PACKAGE, session->handle, opus_pt, dc_answered ? "YES" : "NO");
	/* Full answer dump (the plugin's answer, before the core's ICE/DTLS merge)
	 * for capture against a live viewer. */
	JANUS_LOG(LOG_INFO, "[%s-%p] ==== ANSWER SDP ====\n%s==== END ANSWER SDP ====\n",
		JANUS_SLVOICE_PACKAGE, session->handle, new_sdp);
	if(has_dc && !dc_answered)
		JANUS_LOG(LOG_WARN, "[%s-%p] Offer had a data channel but the answer did NOT accept it — "
			"the viewer will tear down (check Janus was built with SCTP)\n",
			JANUS_SLVOICE_PACKAGE, session->handle);
	return TRUE;
}

/* Build a participant list entry {id, display, setup, muted} (audiobridge shape) */
static json_t *janus_slvoice_participant_summary(janus_slvoice_session *p) {
	json_t *pl = json_object();
	json_object_set_new(pl, "id", json_integer((json_int_t)p->user_id));
	if(p->display)
		json_object_set_new(pl, "display", json_string(p->display));
	json_object_set_new(pl, "setup", g_atomic_int_get(&p->webrtc_up) ? json_true() : json_false());
	json_object_set_new(pl, "muted",
		(p->last_data_fields & SLV_FIELD_M) && p->last_data.m ? json_true() : json_false());
	return pl;
}

/* ---- Async request handler ----------------------------------------------- */

static void *janus_slvoice_handler(void *data) {
	JANUS_LOG(LOG_VERB, "[%s] Joining handler thread\n", JANUS_SLVOICE_PACKAGE);
	janus_slvoice_message *msg = NULL;
	int error_code = 0;
	char error_cause[512];

	while(g_atomic_int_get(&initialized) && !g_atomic_int_get(&stopping)) {
		msg = g_async_queue_pop(messages);
		if(msg == &exit_message)
			break;
		if(msg->handle == NULL) {
			janus_slvoice_message_free(msg);
			msg = NULL;
			continue;
		}
		janus_slvoice_session *session = (janus_slvoice_session *)msg->handle->plugin_handle;
		if(session == NULL || g_atomic_int_get(&session->destroyed)) {
			janus_slvoice_message_free(msg);
			msg = NULL;
			continue;
		}

		error_code = 0;
		error_cause[0] = '\0';
		json_t *root = msg->message;
		json_t *event = NULL;        /* the plugin event JSON to push */
		json_t *jsep_answer = NULL;  /* optional JSEP answer to push alongside */
		/* Deferred presence push (done after locks are released) */
		janus_slvoice_room *presence_room = NULL;
		char *presence_who = NULL;
		gboolean presence_join = FALSE;

		json_t *request = json_object_get(root, "request");
		if(!json_is_string(request)) {
			error_code = JANUS_SLVOICE_ERROR_MISSING_ELEMENT;
			g_snprintf(error_cause, 512, "Missing/!string 'request'");
			goto respond;
		}
		const char *request_text = json_string_value(request);
		JANUS_LOG(LOG_INFO, "[%s-%p] Handling request '%s'\n", JANUS_SLVOICE_PACKAGE, msg->handle, request_text);

		if(!strcasecmp(request_text, "create")) {
			json_t *room_j = json_object_get(root, "room");
			guint64 room_id = (room_j && json_is_integer(room_j)) ? (guint64)json_integer_value(room_j) : 0;
			if(room_id == 0)
				room_id = janus_random_uint64();
			const char *desc = json_string_value(json_object_get(root, "description"));
			gboolean is_private = json_is_true(json_object_get(root, "is_private"));
			gboolean spatial = json_is_true(json_object_get(root, "spatial_audio"));
			json_t *rate_j = json_object_get(root, "sampling_rate");
			guint32 rate = (rate_j && json_is_integer(rate_j)) ? (guint32)json_integer_value(rate_j) : 48000;
			gboolean permanent = json_is_true(json_object_get(root, "permanent"));

			janus_mutex_lock(&rooms_mutex);
			janus_slvoice_room *room = g_hash_table_lookup(rooms, &room_id);
			if(room != NULL) {
				/* Match audiobridge exactly: an existing room is error 486. The
				 * C# side (CreateRoom) treats 486 as success — every region that
				 * hashes to the same room number relies on this (§3.3). */
				janus_mutex_unlock(&rooms_mutex);
				error_code = JANUS_SLVOICE_ERROR_ROOM_EXISTS;
				g_snprintf(error_cause, 512, "Room %"PRIu64" already exists", room_id);
				JANUS_LOG(LOG_INFO, "[%s] Room %"PRIu64" already exists (error 486)\n",
					JANUS_SLVOICE_PACKAGE, room_id);
				goto respond;
			}
			room = janus_slvoice_room_create(room_id, desc, is_private, rate, spatial, permanent);
			guint64 *key = g_malloc(sizeof(guint64));
			*key = room_id;
			g_hash_table_insert(rooms, key, room);
			JANUS_LOG(LOG_INFO, "[%s] Created room %"PRIu64" (%s)\n",
				JANUS_SLVOICE_PACKAGE, room_id, room->description);
			janus_mutex_unlock(&rooms_mutex);

			event = json_object();
			json_object_set_new(event, "audiobridge", json_string("created"));
			json_object_set_new(event, "room", json_integer((json_int_t)room_id));
			json_object_set_new(event, "permanent", permanent ? json_true() : json_false());
			goto respond;
		} else if(!strcasecmp(request_text, "destroy")) {
			json_t *room_j = json_object_get(root, "room");
			if(!json_is_integer(room_j)) {
				error_code = JANUS_SLVOICE_ERROR_MISSING_ELEMENT;
				g_snprintf(error_cause, 512, "Missing/!integer 'room'");
				goto respond;
			}
			guint64 room_id = (guint64)json_integer_value(room_j);
			janus_mutex_lock(&rooms_mutex);
			janus_slvoice_room *room = g_hash_table_lookup(rooms, &room_id);
			if(room == NULL) {
				janus_mutex_unlock(&rooms_mutex);
				error_code = JANUS_SLVOICE_ERROR_NO_SUCH_ROOM;
				g_snprintf(error_cause, 512, "No such room (%"PRIu64")", room_id);
				goto respond;
			}
			g_atomic_int_set(&room->destroyed, 1);
			/* Stop the mix tick thread before evicting (join it while holding
			 * rooms_mutex but NOT room->mutex — the ticker takes room->mutex). */
			janus_slvoice_room_stop(room);
			/* Evict participants: each session we transition out drops the ref
			 * it held (mutex makes the transition unique). */
			janus_mutex_lock(&room->mutex);
			GList *members = g_hash_table_get_values(room->participants);
			GList *mi = members;
			while(mi != NULL) {
				janus_slvoice_session *p = (janus_slvoice_session *)mi->data;
				mi = mi->next;
				gboolean unref = FALSE;
				janus_mutex_lock(&p->mutex);
				if(p->room == room) {
					p->room = NULL;
					unref = TRUE;
				}
				janus_mutex_unlock(&p->mutex);
				if(unref)
					janus_refcount_decrease(&room->ref);
			}
			g_list_free(members);
			g_hash_table_remove_all(room->participants);
			slv_deferred_free_all(&room->vis_deferred);   /* drop deferred columns for this destroyed room */
			janus_mutex_unlock(&room->mutex);
			g_hash_table_remove(rooms, &room_id);
			janus_mutex_unlock(&rooms_mutex);
			janus_refcount_decrease(&room->ref);

			event = json_object();
			json_object_set_new(event, "audiobridge", json_string("destroyed"));
			json_object_set_new(event, "room", json_integer((json_int_t)room_id));
			goto respond;
		} else if(!strcasecmp(request_text, "join")) {
			json_t *room_j = json_object_get(root, "room");
			if(!json_is_integer(room_j)) {
				error_code = JANUS_SLVOICE_ERROR_MISSING_ELEMENT;
				g_snprintf(error_cause, 512, "Missing/!integer 'room'");
				goto respond;
			}
			guint64 room_id = (guint64)json_integer_value(room_j);
			const char *display = json_string_value(json_object_get(root, "display"));

			janus_mutex_lock(&session->mutex);
			gboolean already = (session->room != NULL);
			janus_mutex_unlock(&session->mutex);
			if(already) {
				error_code = JANUS_SLVOICE_ERROR_ALREADY_JOINED;
				g_snprintf(error_cause, 512, "Already joined a room");
				goto respond;
			}
			janus_slvoice_room *room = janus_slvoice_room_ref_by_id(room_id);
			if(room == NULL) {
				error_code = JANUS_SLVOICE_ERROR_NO_SUCH_ROOM;
				g_snprintf(error_cause, 512, "No such room (%"PRIu64")", room_id);
				goto respond;
			}
			if(msg->jsep == NULL) {
				janus_refcount_decrease(&room->ref);
				error_code = JANUS_SLVOICE_ERROR_INVALID_SDP;
				g_snprintf(error_cause, 512, "join requires a JSEP offer");
				goto respond;
			}
			/* Join-time capacity cap (item 1, docs/voice/scaling-assessment.md).
			 * Reject the (cap+1)th joiner BEFORE negotiate/media_alloc so nothing needs
			 * undoing. This REPLACES the old silent, non-deterministic tick-time
			 * truncation (:2157): admission is now deterministic (first-come-first-served
			 * by join order) and distinguishable (ROOM_FULL) so the sim connector can
			 * surface it to the user.
			 *
			 * This is a SAFETY VALVE against unbounded participant growth, NOT a
			 * performance guarantee. It bounds participant COUNT only. The all-audible
			 * load case — every listener within earshot of a talker, ~SLV_MAX_MIX
			 * encodes per tick, over the 20ms budget — is guarded by NOTHING yet. Those
			 * are two different failure modes and only one has a guard: 110 fits under
			 * realistic spread geometry but not under all-audible clustering, so no
			 * single participant number is correct in both cases. */
			janus_mutex_lock(&room->mutex);
			guint room_pop = g_hash_table_size(room->participants);
			janus_mutex_unlock(&room->mutex);
			if(room_pop >= SLV_MAX_MIX) {
				janus_refcount_decrease(&room->ref);
				error_code = JANUS_SLVOICE_ERROR_ROOM_FULL;
				g_snprintf(error_cause, 512, "Room %"PRIu64" is at capacity (%u/%d)",
					room_id, room_pop, SLV_MAX_MIX);
				goto respond;
			}
			char *answer_sdp = NULL;
			if(!janus_slvoice_negotiate(session, msg->jsep, &answer_sdp, &error_code, error_cause)) {
				janus_refcount_decrease(&room->ref);
				goto respond;
			}
			/* Commit membership (room ref transfers to the session) */
			guint64 user_id = janus_random_uint64();
			if(user_id == 0) user_id = 1;
			janus_mutex_lock(&session->mutex);
			session->user_id = user_id;
			g_free(session->display);
			session->display = display ? g_strdup(display) : NULL;
			session->room = room;
			/* Preallocate all codec + mix buffers now, at join — the tick never
			 * allocates. Failure is logged but non-fatal (participant holds the
			 * session, just contributes/receives silence). */
			if(!janus_slvoice_media_alloc_locked(session))
				JANUS_LOG(LOG_ERR, "[%s-%p] Media allocation failed at join; participant will be silent\n",
					JANUS_SLVOICE_PACKAGE, msg->handle);
			janus_mutex_unlock(&session->mutex);
			janus_mutex_lock(&room->mutex);
			guint64 *key = g_malloc(sizeof(guint64));
			*key = user_id;
			g_hash_table_insert(room->participants, key, session);
			/* Join-window fix: replay any columns deferred for this listener BEFORE the join roster
			 * below (and before push_presence / the data_ready backlog) reads its exclusion/mute
			 * state, so a ban/mute that arrived pre-join is in force first. */
			janus_slvoice_room_replay_deferred_locked(room, session);
			json_t *list = json_array();
			GHashTableIter iter;
			gpointer value;
			g_hash_table_iter_init(&iter, room->participants);
			while(g_hash_table_iter_next(&iter, NULL, &value)) {
				janus_slvoice_session *p = value;
				if(p == session)
					continue;
				/* Join-time duplicate-display detection (§M / KnownDefects teardown
				 * ordering item 1, approach (a)+): a display (avatar UUID) already in
				 * the room means an orphaned handle or a relog overlap. DETECTION
				 * ONLY — the joiner is admitted and nothing is evicted (the plugin
				 * cannot notify an evicted viewer, so evicting a live session is an
				 * unrecoverable outage). Mirrors the fan-out collision WARN
				 * (apply_visbatch), which only fires when a batch names the avatar
				 * as listener; this one fires at the moment the duplicate is born.
				 * The liveness triple (webrtc_up, rtp_in_count, idle) plus age is
				 * logged to build the evidence for whether a future CONDITIONED
				 * eviction (webrtc_up==0 && old) would ever have hit a live session.
				 * Runs under room->mutex, already held; webrtc_up is an atomic and
				 * created_ts is immutable after create; rtp_in_count/last_rtp_us are
				 * unlocked diagnostic reads (torn values only garble a log line). */
				if(display != NULL && p->display != NULL && !strcmp(p->display, display)) {
					gint64 dnow = janus_get_monotonic_time();
					JANUS_LOG(LOG_WARN, "[%s] join: display %s already in room %"PRIu64
						" — duplicate display; admitted anyway, no eviction. new user_id %"PRIu64
						", existing user_id %"PRIu64" (webrtc_up=%d, rtp_in_count=%"PRIu64
						", rtp_idle=%.1fs, age=%.1fs) (parcel-voice-semantics.md §M)\n",
						JANUS_SLVOICE_PACKAGE, display, room_id, user_id, p->user_id,
						g_atomic_int_get(&p->webrtc_up) ? 1 : 0, p->rtp_in_count,
						p->last_rtp_us ? (double)(dnow - p->last_rtp_us) / (double)G_USEC_PER_SEC : -1.0,
						(double)(dnow - p->created_ts) / (double)G_USEC_PER_SEC);
				}
				/* Phase 3a: the initial roster (join-backlog) a newly-connecting
				 * listener receives is filtered by the SAME exclusion predicate the
				 * live join/leave, power batch, and mix cull use — a speaker this
				 * listener excludes is omitted from its initial roster, exactly as a
				 * live join would be suppressed. (Read under room->mutex, which we
				 * hold; the batch apply also takes room->mutex, so no race.) */
				if(slv_roster_excludes(session->excluded, p->display))
					continue;
				json_array_append_new(list, janus_slvoice_participant_summary(p));
			}
			janus_mutex_unlock(&room->mutex);

			event = json_object();
			json_object_set_new(event, "audiobridge", json_string("joined"));
			json_object_set_new(event, "room", json_integer((json_int_t)room_id));
			json_object_set_new(event, "id", json_integer((json_int_t)user_id));
			json_object_set_new(event, "participants", list);
			jsep_answer = json_pack("{ssss}", "type", "answer", "sdp", answer_sdp);
			g_free(answer_sdp);
			/* Notify the other participants that this peer joined (after unlock) */
			if(display != NULL) {
				presence_room = room;   /* borrowed; still referenced by the session */
				presence_who = g_strdup(display);
				presence_join = TRUE;
			}
			JANUS_LOG(LOG_INFO, "[%s-%p] Participant %"PRIu64" (%s) joined room %"PRIu64"\n",
				JANUS_SLVOICE_PACKAGE, msg->handle, user_id, display ? display : "??", room_id);
			goto respond;
		} else if(!strcasecmp(request_text, "configure")) {
			if(msg->jsep != NULL) {
				char *answer_sdp = NULL;
				if(!janus_slvoice_negotiate(session, msg->jsep, &answer_sdp, &error_code, error_cause))
					goto respond;
				jsep_answer = json_pack("{ssss}", "type", "answer", "sdp", answer_sdp);
				g_free(answer_sdp);
			}
			event = json_object();
			json_object_set_new(event, "audiobridge", json_string("event"));
			json_object_set_new(event, "result", json_string("ok"));
			goto respond;
		} else if(!strcasecmp(request_text, "leave")) {
			janus_mutex_lock(&session->mutex);
			gboolean joined = (session->room != NULL);
			guint64 room_id = session->room ? session->room->room_id : 0;
			guint64 user_id = session->user_id;
			char *who = session->display ? g_strdup(session->display) : NULL;
			janus_slvoice_room *room = session->room;
			janus_mutex_unlock(&session->mutex);
			if(!joined) {
				g_free(who);
				error_code = JANUS_SLVOICE_ERROR_NOT_JOINED;
				g_snprintf(error_cause, 512, "Not in a room");
				goto respond;
			}
			/* Notify others BEFORE we remove ourselves */
			if(who != NULL && room != NULL)
				janus_slvoice_push_presence(room, who, FALSE);
			g_free(who);
			janus_slvoice_leave_room(session);
			janus_mutex_lock(&session->mutex);
			janus_slvoice_media_free_locked(session);
			janus_mutex_unlock(&session->mutex);

			event = json_object();
			json_object_set_new(event, "audiobridge", json_string("left"));
			json_object_set_new(event, "room", json_integer((json_int_t)room_id));
			json_object_set_new(event, "id", json_integer((json_int_t)user_id));
			goto respond;
		} else if(!strcasecmp(request_text, "list")) {
			json_t *list = json_array();
			janus_mutex_lock(&rooms_mutex);
			GHashTableIter iter;
			gpointer value;
			g_hash_table_iter_init(&iter, rooms);
			while(g_hash_table_iter_next(&iter, NULL, &value)) {
				janus_slvoice_room *room = value;
				if(room == NULL || g_atomic_int_get(&room->destroyed) || room->is_private)
					continue;
				json_t *rl = json_object();
				json_object_set_new(rl, "room", json_integer((json_int_t)room->room_id));
				json_object_set_new(rl, "description", json_string(room->description));
				json_object_set_new(rl, "sampling_rate", json_integer(room->sampling_rate));
				json_object_set_new(rl, "spatial_audio", room->spatial_audio ? json_true() : json_false());
				janus_mutex_lock(&room->mutex);
				json_object_set_new(rl, "num_participants", json_integer(g_hash_table_size(room->participants)));
				janus_mutex_unlock(&room->mutex);
				json_array_append_new(list, rl);
			}
			janus_mutex_unlock(&rooms_mutex);
			event = json_object();
			json_object_set_new(event, "audiobridge", json_string("success"));
			json_object_set_new(event, "list", list);
			goto respond;
		} else if(!strcasecmp(request_text, "listparticipants")) {
			json_t *room_j = json_object_get(root, "room");
			if(!json_is_integer(room_j)) {
				error_code = JANUS_SLVOICE_ERROR_MISSING_ELEMENT;
				g_snprintf(error_cause, 512, "Missing/!integer 'room'");
				goto respond;
			}
			guint64 room_id = (guint64)json_integer_value(room_j);
			janus_slvoice_room *room = janus_slvoice_room_ref_by_id(room_id);
			if(room == NULL) {
				error_code = JANUS_SLVOICE_ERROR_NO_SUCH_ROOM;
				g_snprintf(error_cause, 512, "No such room (%"PRIu64")", room_id);
				goto respond;
			}
			json_t *list = json_array();
			janus_mutex_lock(&room->mutex);
			GHashTableIter iter;
			gpointer value;
			g_hash_table_iter_init(&iter, room->participants);
			while(g_hash_table_iter_next(&iter, NULL, &value)) {
				json_array_append_new(list, janus_slvoice_participant_summary((janus_slvoice_session *)value));
			}
			janus_mutex_unlock(&room->mutex);
			janus_refcount_decrease(&room->ref);
			event = json_object();
			json_object_set_new(event, "audiobridge", json_string("participants"));
			json_object_set_new(event, "room", json_integer((json_int_t)room_id));
			json_object_set_new(event, "participants", list);
			goto respond;
		} else {
			error_code = JANUS_SLVOICE_ERROR_INVALID_REQUEST;
			g_snprintf(error_cause, 512, "Unknown request '%s'", request_text);
			goto respond;
		}

respond:
		if(error_code != 0) {
			if(event) { json_decref(event); event = NULL; }
			if(jsep_answer) { json_decref(jsep_answer); jsep_answer = NULL; }
			event = json_object();
			json_object_set_new(event, "audiobridge", json_string("event"));
			json_object_set_new(event, "error_code", json_integer(error_code));
			json_object_set_new(event, "error", json_string(error_cause));
			JANUS_LOG(LOG_WARN, "[%s-%p] Request error %d: %s\n",
				JANUS_SLVOICE_PACKAGE, msg->handle, error_code, error_cause);
		}
		int ret = gateway->push_event(msg->handle, &janus_slvoice_plugin, msg->transaction, event, jsep_answer);
		JANUS_LOG(LOG_VERB, "[%s-%p] Pushed event: %d\n", JANUS_SLVOICE_PACKAGE, msg->handle, ret);
		json_decref(event);
		if(jsep_answer)
			json_decref(jsep_answer);
		/* Deferred join presence notice (after the response is sent) */
		if(presence_room != NULL && presence_who != NULL)
			janus_slvoice_push_presence(presence_room, presence_who, presence_join);
		g_free(presence_who);
		janus_slvoice_message_free(msg);
		msg = NULL;
	}
	JANUS_LOG(LOG_VERB, "[%s] Leaving handler thread\n", JANUS_SLVOICE_PACKAGE);
	return NULL;
}

/* ---- Mixer->client periodic power/VAD ticker -----------------------------
 * Every ~100ms (spec §9) push each room's participants a well-formed per-peer
 * batch keyed by display (agent UUID): { "<uuid>": {"p":<RMS*128>,"V":<VAD>} }.
 * Phase 1A has no audio, so p=0 and V=false for everyone — the point is that the
 * viewer receives valid state and renders a (silent) voice dot. */
static void *janus_slvoice_sender(void *data) {
	JANUS_LOG(LOG_VERB, "[%s] Joining sender thread\n", JANUS_SLVOICE_PACKAGE);
	while(g_atomic_int_get(&initialized) && !g_atomic_int_get(&stopping)) {
		g_usleep(SLV_POWER_TICK_MS * 1000);
		if(g_atomic_int_get(&stopping) || !g_atomic_int_get(&initialized))
			break;
		janus_mutex_lock(&rooms_mutex);
		GHashTableIter riter;
		gpointer rvalue;
		g_hash_table_iter_init(&riter, rooms);
		while(g_hash_table_iter_next(&riter, NULL, &rvalue)) {
			janus_slvoice_room *room = rvalue;
			if(room == NULL || g_atomic_int_get(&room->destroyed))
				continue;
			janus_mutex_lock(&room->mutex);
			if(g_hash_table_size(room->participants) == 0) {
				janus_mutex_unlock(&room->mutex);
				continue;
			}
			/* One batch for the whole room. p = level*128, v = VAD — both real,
			 * computed by the mix tick from each ACTIVE participant's decoded
			 * frame (so OTHER avatars' dots animate, spec §4.1 / req 5). NOTE the
			 * VAD key is lowercase "v": the viewer reads participant_obj["v"] into
			 * mIsSpeaking (llvoicewebrtc.cpp); the old uppercase "V" was never
			 * consumed, so the speaking state never lit. */
			gint64 now = janus_get_monotonic_time();
			json_t *batch = json_object();
			/* §M dot-batch collapse fix: `batch` is keyed by display, and jansson
			 * replaces on duplicate key — so two same-display sessions (orphan +
			 * live after a relog) used to collapse LAST-WRITE-WINS in undefined
			 * GLib iteration order, and an orphan's {p:0,v:false} could darken a
			 * speaking avatar's indicator. Deterministic merge instead: a
			 * webrtc_up handle always beats a downed one (the observed failure is
			 * precisely a downed orphan winning); among handles of EQUAL liveness
			 * take max power and OR the VAD, which is order-independent for the
			 * brief both-live relog overlap. dot_up tracks the current winner's
			 * liveness per display (borrowed keys — display strings are owned by
			 * sessions and stable under room->mutex, held here). */
			GHashTable *dot_up = g_hash_table_new(g_str_hash, g_str_equal);
			GHashTableIter piter;
			gpointer pvalue;
			g_hash_table_iter_init(&piter, room->participants);
			while(g_hash_table_iter_next(&piter, NULL, &pvalue)) {
				janus_slvoice_session *p = pvalue;
				/* Refresh the RTP in/out rate estimate about once per second. */
				janus_mutex_lock(&p->mutex);
				if(p->rate_ts == 0)
					p->rate_ts = now;
				gint64 dt = now - p->rate_ts;
				if(dt >= G_USEC_PER_SEC) {
					double secs = (double)dt / (double)G_USEC_PER_SEC;
					p->rtp_in_rate = (double)(p->rtp_in_count - p->rtp_in_prev) / secs;
					p->rtp_out_rate = (double)(p->rtp_out_count - p->rtp_out_prev) / secs;
					p->rtp_in_prev = p->rtp_in_count;
					p->rtp_out_prev = p->rtp_out_count;
					p->rate_ts = now;
				}
				janus_mutex_unlock(&p->mutex);
				if(p->display == NULL)
					continue;
				int ppow = g_atomic_int_get(&p->power_p);          /* level*128 */
				gboolean pvad = g_atomic_int_get(&p->vad) ? TRUE : FALSE;
				gboolean pup = g_atomic_int_get(&p->webrtc_up) ? TRUE : FALSE;
				json_t *prev = json_object_get(batch, p->display);
				if(prev == NULL) {
					json_t *pv = json_object();
					json_object_set_new(pv, "p", json_integer(ppow));
					json_object_set_new(pv, "v", pvad ? json_true() : json_false());
					json_object_set_new(batch, p->display, pv);
					g_hash_table_insert(dot_up, p->display, GINT_TO_POINTER(pup ? 1 : 0));
				} else {
					gboolean wup = GPOINTER_TO_INT(g_hash_table_lookup(dot_up, p->display)) ? TRUE : FALSE;
					if(pup && !wup) {
						/* A live handle replaces a downed winner outright. */
						json_t *pv = json_object();
						json_object_set_new(pv, "p", json_integer(ppow));
						json_object_set_new(pv, "v", pvad ? json_true() : json_false());
						json_object_set_new(batch, p->display, pv);
						g_hash_table_insert(dot_up, p->display, GINT_TO_POINTER(1));
					} else if(pup == wup) {
						/* Equal liveness: max power, OR vad — order-independent. */
						if((json_int_t)ppow > json_integer_value(json_object_get(prev, "p")))
							json_object_set_new(prev, "p", json_integer(ppow));
						if(pvad)
							json_object_set_new(prev, "v", json_true());
					}
					/* pup < wup: a downed handle never overwrites a live one. */
				}
			}
			g_hash_table_destroy(dot_up);
			/* Send per listener. A listener with NO exclusions gets the shared full
			 * batch (fast path — the common case). A listener with exclusions gets a
			 * filtered copy omitting excluded sources' {p,v} dots — roster omission
			 * from the SAME set that culls its audio (single source of truth §1).
			 * p->excluded is read under room->mutex, which we hold here; the batch
			 * apply also takes room->mutex, so the two never overlap. */
			char *full_text = json_dumps(batch, JSON_COMPACT);
			size_t full_len = full_text ? strlen(full_text) : 0;
			g_hash_table_iter_init(&piter, room->participants);
			while(g_hash_table_iter_next(&piter, NULL, &pvalue)) {
				janus_slvoice_session *p = pvalue;
				if(!g_atomic_int_get(&p->dc_open))
					continue;
				if(p->excluded == NULL || g_hash_table_size(p->excluded) == 0) {
					if(full_text != NULL && full_len < 65536) {
						janus_plugin_data d = { .label = NULL, .protocol = NULL, .binary = FALSE,
							.buffer = full_text, .length = (uint16_t)full_len };
						gateway->relay_data(p->handle, &d);
					}
					continue;
				}
				json_t *filt = json_object();
				const char *bk;
				json_t *bv;
				json_object_foreach(batch, bk, bv) {
					if(slv_roster_excludes(p->excluded, bk))
						continue;   /* omit an excluded source's dot for this listener */
					json_object_set(filt, bk, bv);   /* increfs bv */
				}
				char *ftext = json_dumps(filt, JSON_COMPACT);
				if(ftext != NULL) {
					size_t flen = strlen(ftext);
					if(flen < 65536) {
						janus_plugin_data d = { .label = NULL, .protocol = NULL, .binary = FALSE,
							.buffer = ftext, .length = (uint16_t)flen };
						gateway->relay_data(p->handle, &d);
					}
					free(ftext);
				}
				json_decref(filt);
			}
			if(full_text != NULL)
				free(full_text);
			json_decref(batch);
			janus_mutex_unlock(&room->mutex);
		}
		janus_mutex_unlock(&rooms_mutex);
	}
	JANUS_LOG(LOG_VERB, "[%s] Leaving sender thread\n", JANUS_SLVOICE_PACKAGE);
	return NULL;
}

/* ---- Phase 2 media: buffers, jitter buffer, per-room mix tick ------------- */

/* Free all codec + mix buffers for a session. session->mutex held. */
static void janus_slvoice_media_free_locked(janus_slvoice_session *s) {
	if(s->dec) { opus_decoder_destroy(s->dec); s->dec = NULL; }
	if(s->enc) { opus_encoder_destroy(s->enc); s->enc = NULL; }
	g_free(s->jb);       s->jb = NULL;
	g_free(s->jb_seq);   s->jb_seq = NULL;
	g_free(s->jb_len);   s->jb_len = NULL;
	g_free(s->ring);     s->ring = NULL;
	g_free(s->decbuf);   s->decbuf = NULL;
	g_free(s->framebuf); s->framebuf = NULL;
	g_free(s->outbuf);   s->outbuf = NULL;
	s->ring_wpos = 0;
	s->dec_samples = 0;
	s->jb_primed = FALSE;
	s->jb_have_first = FALSE;
	s->media_ready = FALSE;
	g_atomic_int_set(&s->echo_active, 0);
	g_atomic_int_set(&s->active, 0);
	g_atomic_int_set(&s->power_p, 0);
	g_atomic_int_set(&s->vad, 0);
}

/* Allocate codecs + all mix buffers ONCE, at join. Nothing else allocates on
 * the media path — the tick and incoming_rtp are allocation-free. session->mutex
 * held. Returns FALSE (and frees) on failure. */
static gboolean janus_slvoice_media_alloc_locked(janus_slvoice_session *s) {
	if(s->media_ready && s->dec && s->enc)
		return TRUE;
	int err = 0;
	s->dec = opus_decoder_create(SLV_RATE, SLV_CHANNELS, &err);
	if(err != OPUS_OK || s->dec == NULL) {
		JANUS_LOG(LOG_ERR, "[%s] opus_decoder_create failed: %s\n", JANUS_SLVOICE_PACKAGE, opus_strerror(err));
		janus_slvoice_media_free_locked(s);
		return FALSE;
	}
	err = 0;
	s->enc = opus_encoder_create(SLV_RATE, SLV_CHANNELS, OPUS_APPLICATION_VOIP, &err);
	if(err != OPUS_OK || s->enc == NULL) {
		JANUS_LOG(LOG_ERR, "[%s] opus_encoder_create failed: %s\n", JANUS_SLVOICE_PACKAGE, opus_strerror(err));
		janus_slvoice_media_free_locked(s);
		return FALSE;
	}
	opus_encoder_ctl(s->enc, OPUS_SET_MAX_BANDWIDTH(OPUS_BANDWIDTH_FULLBAND));
	opus_encoder_ctl(s->enc, OPUS_SET_INBAND_FEC(1));
	opus_encoder_ctl(s->enc, OPUS_SET_PACKET_LOSS_PERC(10));
	opus_encoder_ctl(s->enc, OPUS_SET_COMPLEXITY(SLV_OPUS_COMPLEXITY));
	opus_encoder_ctl(s->enc, OPUS_SET_BITRATE(SLV_OPUS_BITRATE));   /* spec §5: listener mix 64-96kbps stereo */
	opus_encoder_ctl(s->enc, OPUS_SET_DTX(1));            /* encode-skip: cheap comfort frames on silence */
	/* Preallocate every buffer used by the tick — nothing is allocated per packet
	 * or per tick. The 240KB echo delay ring is the ONE exception: it is used only
	 * by build_echo on the echo path, so it is allocated lazily on first echo enable
	 * (see janus_slvoice_echo_start_locked) rather than for every session here. */
	s->jb       = g_malloc0((gsize)SLV_JB_SLOTS * SLV_JB_PAYLOAD_MAX);
	s->jb_seq   = g_malloc0((gsize)SLV_JB_SLOTS * sizeof(uint16_t));
	s->jb_len   = g_malloc0((gsize)SLV_JB_SLOTS * sizeof(int));
	s->decbuf   = g_malloc0((gsize)SLV_DECODE_MAX * SLV_CHANNELS * sizeof(float));
	s->framebuf = g_malloc0((gsize)SLV_FRAME_SAMPLES * SLV_CHANNELS * sizeof(float));
	s->outbuf   = g_malloc0(SLV_RTP_OUT_MAX);
	s->ring_wpos = 0;
	s->dec_samples = 0;
	s->jb_primed = FALSE;
	s->jb_have_first = FALSE;
	s->first_frame_logged = FALSE;
	s->frames_decoded = 0;
	s->frames_encoded = 0;
	s->frames_mixed = 0;
	s->rtp_out_count = 0;
	s->silent_ticks = 0;
	s->last_rms = 0.0;
	s->last_mix_rms = 0.0;
	s->last_mix_rms_l = 0.0;
	s->last_mix_rms_r = 0.0;
	s->decode_ok = FALSE;
	g_atomic_int_set(&s->power_p, 0);
	g_atomic_int_set(&s->vad, 0);
	g_atomic_int_set(&s->active, 0);
	s->out_seq  = (uint16_t)(janus_random_uint64() & 0xFFFF);
	s->out_ts   = (uint32_t)(janus_random_uint64() & 0xFFFFFFFF);
	s->out_ssrc = (uint32_t)(janus_random_uint64() & 0xFFFFFFFF);
	s->media_ready = TRUE;
	return TRUE;
}

/* Echo is a per-listener OUTPUT override, not a resource owner: start/stop flip
 * the flag and clear the delay ring. The ring itself is allocated lazily here on
 * first enable (it is 240KB and used only on the echo path); it then lives for
 * the rest of the session and is freed only at teardown in media_free_locked.
 * session->mutex held. */
static void janus_slvoice_echo_start_locked(janus_slvoice_session *s) {
	if(!s->media_ready)
		janus_slvoice_media_alloc_locked(s);
	/* Lazy ring allocation. Safe because this runs under session->mutex — the SAME
	 * lock build_echo holds in the tick's Pass 2 (:2237). So the ring can never be
	 * allocated while build_echo reads it, and the tick observing echo_active set
	 * (:2238) implies this allocation already completed under the lock, so build_echo
	 * never sees s->ring == NULL. The enable path (incoming_data / setup_media) takes
	 * ONLY session->mutex, never room->mutex, so it cannot invert the tick's
	 * room->mutex -> session->mutex order. Off the tick path, so it does not break the
	 * tick's allocation-free discipline. */
	if(s->ring == NULL)
		s->ring = g_malloc0((gsize)SLV_RING_SAMPLES * SLV_CHANNELS * sizeof(float));
	if(s->ring)
		memset(s->ring, 0, (gsize)SLV_RING_SAMPLES * SLV_CHANNELS * sizeof(float));
	s->ring_wpos = 0;
	g_atomic_int_set(&s->echo_active, 1);
}

static void janus_slvoice_echo_stop_locked(janus_slvoice_session *s) {
	g_atomic_int_set(&s->echo_active, 0);
	if(s->ring)
		memset(s->ring, 0, (gsize)SLV_RING_SAMPLES * SLV_CHANNELS * sizeof(float));
	s->ring_wpos = 0;
}

/* Insert one RTP payload into the fixed-lag jitter buffer by sequence number.
 * Playout is driven by the tick (janus_slvoice_jb_pull), NOT here. session->mutex held. */
static void janus_slvoice_jb_insert(janus_slvoice_session *s, uint16_t seq,
		const unsigned char *payload, int plen) {
	if(s->jb == NULL)
		return;
	if(plen > 0 && plen <= SLV_JB_PAYLOAD_MAX) {
		int slot = seq % SLV_JB_SLOTS;
		memcpy(s->jb + (gsize)slot * SLV_JB_PAYLOAD_MAX, payload, plen);
		s->jb_seq[slot] = seq;
		s->jb_len[slot] = plen;
	}
	if(!s->jb_have_first) {
		s->jb_have_first = TRUE;
		s->jb_first_seq = seq;
		s->jb_newest = seq;
	} else if((int16_t)(seq - s->jb_newest) > 0) {
		s->jb_newest = seq;
	}
}

/* Playout status of one tick's pull from the jitter buffer. */
enum { SLV_JB_SILENCE = 0, SLV_JB_PLC = 1, SLV_JB_FRAME = 2 };

/* Pull the next playout frame for this tick. Returns SLV_JB_FRAME (with pl/pn
 * set to an Opus payload to decode), SLV_JB_PLC (decode a concealment frame), or
 * SLV_JB_SILENCE (no data yet / source paused — contribute nothing). The fixed
 * lag (SLV_JB_LAG) gives a reorder cushion; a long stall resyncs the cursor so a
 * talker resuming from DTX does not drown in PLC. session->mutex held. */
static int janus_slvoice_jb_pull(janus_slvoice_session *s, const unsigned char **pl, int *pn) {
	*pl = NULL; *pn = 0;
	if(!s->jb_have_first)
		return SLV_JB_SILENCE;
	if(!s->jb_primed) {
		if((int16_t)(s->jb_newest - s->jb_first_seq) < SLV_JB_LAG)
			return SLV_JB_SILENCE;   /* still filling the initial cushion */
		s->jb_primed = TRUE;
		s->jb_next = s->jb_first_seq;
	}
	int16_t lead = (int16_t)(s->jb_newest - s->jb_next);
	if(lead < 0)
		return SLV_JB_SILENCE;       /* played past the newest — source paused */
	if(lead > SLV_JB_MAX_LEAD)
		s->jb_next = (uint16_t)(s->jb_newest - SLV_JB_LAG);   /* resync after a long gap */
	int slot = s->jb_next % SLV_JB_SLOTS;
	int status;
	if(s->jb_len[slot] > 0 && s->jb_seq[slot] == s->jb_next) {
		*pl = s->jb + (gsize)slot * SLV_JB_PAYLOAD_MAX;
		*pn = s->jb_len[slot];
		s->jb_len[slot] = 0;         /* consume */
		status = SLV_JB_FRAME;
	} else {
		status = SLV_JB_PLC;         /* missing packet inside the stream */
	}
	s->jb_next++;
	return status;
}

/* Pass 1 of the tick: decode this participant's next source frame (or PLC),
 * update its active-set membership (DTX/VAD cull with release hold) and its
 * power/VAD. Leaves the decoded PCM in s->decbuf / s->dec_samples for pass 2.
 * session->mutex held. */
static void janus_slvoice_tick_decode_locked(janus_slvoice_session *s, gint64 now) {
	gboolean was_active = g_atomic_int_get(&s->active);
	gboolean act = (s->last_rtp_us != 0 &&
		(now - s->last_rtp_us) <= (gint64)SLV_VAD_RELEASE_MS * 1000);
	g_atomic_int_set(&s->active, act ? 1 : 0);
	s->dec_samples = 0;
	if(!s->media_ready || s->dec == NULL || !act) {
		if(!act) {
			g_atomic_int_set(&s->power_p, 0);
			g_atomic_int_set(&s->vad, 0);
		}
		goto membership;
	}
	const unsigned char *pl = NULL;
	int pn = 0;
	int st = janus_slvoice_jb_pull(s, &pl, &pn);
	if(st == SLV_JB_SILENCE) {
		g_atomic_int_set(&s->power_p, 0);
		g_atomic_int_set(&s->vad, 0);
		goto membership;
	}
	int samples = (st == SLV_JB_FRAME)
		? opus_decode_float(s->dec, pl, pn, s->decbuf, SLV_DECODE_MAX, 0)
		: opus_decode_float(s->dec, NULL, 0, s->decbuf, SLV_FRAME_SAMPLES, 0);  /* PLC */
	if(samples < 0) {
		s->decode_ok = FALSE;
		JANUS_LOG(LOG_WARN, "[%s-%p] Opus decode error: %s\n",
			JANUS_SLVOICE_PACKAGE, s->handle, opus_strerror(samples));
		g_atomic_int_set(&s->power_p, 0);
		g_atomic_int_set(&s->vad, 0);
		goto membership;
	}
	s->decode_ok = TRUE;
	if(samples > SLV_DECODE_MAX)
		samples = SLV_DECODE_MAX;
	s->frames_decoded++;
	s->dec_samples = samples;
	if(!s->first_frame_logged) {
		s->first_frame_logged = TRUE;
		JANUS_LOG(LOG_INFO, "[%s-%p] First audio frame decoded (%d samples/ch)\n",
			JANUS_SLVOICE_PACKAGE, s->handle, samples);
	}
	double rms = slv_mix_rms(s->decbuf, (size_t)samples * SLV_CHANNELS);
	s->last_rms = rms;
	int p = (int)(rms * 128.0);
	if(p < 0) p = 0; else if(p > 127) p = 127;
	g_atomic_int_set(&s->power_p, p);
	g_atomic_int_set(&s->vad, rms > SLV_VAD_RMS ? 1 : 0);
membership:
	if((gboolean)g_atomic_int_get(&s->active) != was_active)
		JANUS_LOG(LOG_DBG, "[%s-%p] participant %"PRIu64" %s the active set\n",
			JANUS_SLVOICE_PACKAGE, s->handle, s->user_id,
			g_atomic_int_get(&s->active) ? "entered" : "left");
}

/* Build this listener's 500ms-delayed echo frame from its own decoded input
 * (self-monitor / diagnostics). Always advances the ring by SLV_FRAME_SAMPLES so
 * the delay stays fixed regardless of input. tick-owned; no lock needed. */
static void janus_slvoice_build_echo(janus_slvoice_session *s, float *out, int samples) {
	int wp = s->ring_wpos;
	int rp = (wp + SLV_RING_SAMPLES - SLV_DELAY_SAMPLES) % SLV_RING_SAMPLES;
	int have = s->dec_samples;
	for(int i = 0; i < samples; i++) {
		out[2*i]     = s->ring[2*rp];
		out[2*i + 1] = s->ring[2*rp + 1];
		float l = (i < have) ? s->decbuf[2*i]     : 0.0f;
		float r = (i < have) ? s->decbuf[2*i + 1] : 0.0f;
		s->ring[2*wp]     = l;
		s->ring[2*wp + 1] = r;
		wp = (wp + 1) % SLV_RING_SAMPLES;
		rp = (rp + 1) % SLV_RING_SAMPLES;
	}
	s->ring_wpos = wp;
}

/* Encode this listener's output frame and relay it. On a silent mix the encode
 * is SKIPPED entirely (DTX transmit-suppression): the RTP timestamp still
 * advances so the viewer's playout timeline stays honest, but no packet is sent
 * and no encode cost is paid — so encode cost scales with AUDIBLE listeners, not
 * connected ones (spec §5). tick-owned; no lock needed. */
static void janus_slvoice_encode_relay(janus_slvoice_session *s, float *frame, int samples, gboolean silence) {
	uint32_t ts = s->out_ts;
	s->out_ts += (uint32_t)samples;     /* advance every tick, 48k clock */
	if(silence || s->enc == NULL || s->outbuf == NULL) {
		s->silent_ticks++;
		return;
	}
	int enc_len = opus_encode_float(s->enc, frame, samples, s->outbuf + 12, SLV_RTP_OUT_MAX - 12);
	if(enc_len < 0) {
		JANUS_LOG(LOG_WARN, "[%s-%p] Opus encode error: %s\n",
			JANUS_SLVOICE_PACKAGE, s->handle, opus_strerror(enc_len));
		return;
	}
	if(enc_len <= 2) {                  /* Opus DTX produced a no-data frame */
		s->silent_ticks++;
		return;
	}
	s->silent_ticks = 0;
	s->frames_encoded++;
	janus_rtp_header *rtp = (janus_rtp_header *)s->outbuf;
	memset(rtp, 0, 12);
	rtp->version = 2;
	rtp->type = (s->opus_pt > 0 ? s->opus_pt : 111);
	rtp->markerbit = 0;
	rtp->seq_number = htons(s->out_seq++);   /* only advances on SENT packets */
	rtp->timestamp = htonl(ts);
	rtp->ssrc = htonl(s->out_ssrc);
	janus_plugin_rtp outp = { .mindex = -1, .video = FALSE,
		.buffer = (char *)s->outbuf, .length = (uint16_t)(12 + enc_len) };
	janus_plugin_rtp_extensions_reset(&outp.extensions);
	gateway->relay_rtp(s->handle, &outp);
	s->rtp_out_count++;
}

/* The mix tick: decode every active participant once, then for each listener
 * build a flat N-minus-one mix of the OTHER active participants (honouring that
 * listener's per-source mutes/gains), encode, and relay. An echoed participant
 * hears its own delayed audio instead. Holds room->mutex for the whole tick so
 * membership is frozen and no participant's media is freed mid-tick. */
/* Phase 3b: snapshot this participant's geometry for the tick. Runs in pass 1
 * under s->mutex — the SAME lock last_data is written under by
 * janus_slvoice_incoming_data — so pass 2 reads the snapshot lock-free, exactly
 * as it reads decbuf. Copies sp/lp/lh from the last SLData gated on presence,
 * then applies the §7.1 server-side camera leash to lp (the client clamp at
 * llvoicewebrtc.cpp:1203-1220 is spoofable, so we re-apply it here). Leaves the
 * snapshot marked invalid when the two positions are absent. Snapshot-only:
 * nothing reads snap_* yet (Phase 3b slice one). */
static void janus_slvoice_snapshot_geometry_locked(janus_slvoice_session *s) {
	unsigned f = s->last_data_fields;
	s->snap_valid = ((f & SLV_FIELD_SP) != 0) && ((f & SLV_FIELD_LP) != 0);
	if(!s->snap_valid)
		return;   /* no usable geometry this tick; the mix stays flat */
	s->snap_sp = s->last_data.sp;
	s->snap_lp = s->last_data.lp;
	s->snap_lh = (f & SLV_FIELD_LH) ? s->last_data.lh
	                                : (slv_quat){ 0.0, 0.0, 0.0, 1.0 };   /* identity if no heading */
	/* §7.1 camera leash: clamp lp onto the slv_spatial.leash_dist sphere around sp
	 * when it exceeds it. Projection lifted from llvoicewebrtc.cpp:1213, ×100 unit. */
	slv_vec3 off = slv_vec3_sub(s->snap_lp, s->snap_sp);
	double d = slv_vec3_mag(off);
	if(d > slv_spatial.leash_dist)
		s->snap_lp = slv_vec3_madd(s->snap_sp, slv_spatial.leash_dist / d, off);
}

/* Phase 3b item 2: per-listener distance cull with hysteresis. Returns TRUE if the
 * source `uuid`, at distance `d` from this listener (stored ×100 unit), must be
 * dropped from the mix. Maintains the per-listener hysteresis latch keyed by source
 * UUID: find-or-create the slot, LRU-evicting the oldest seen_tick when full (brief
 * Amendment 2 proves any displaced slot is a departed UUID), stamp it this tick, and
 * latch — an audible pair culls only past the cutoff (slv_spatial.cutoff_dist), a culled
 * pair un-culls only inside the re-add distance (slv_spatial.readd_dist). A pair seen for
 * the first time has no prior state, so it uses
 * a plain threshold at the cutoff (no band). s->mutex held; writes only the listener's
 * own hysteresis table (never s->active / audible / decode). */
static gboolean janus_slvoice_distance_cull_locked(janus_slvoice_session *s,
		const char *uuid, double d, guint64 now_tick) {
	slv_cull_hyst *slot = NULL;
	for(int k = 0; k < s->n_cull_hyst; k++) {
		if(strcmp(s->cull_hyst[k].uuid, uuid) == 0) {
			slot = &s->cull_hyst[k];
			break;
		}
	}
	if(slot == NULL) {
		if(s->n_cull_hyst < SLV_MAX_MIX) {
			slot = &s->cull_hyst[s->n_cull_hyst++];
		} else {
			/* Table full: evict the least-recently-seen slot (Amendment 2). */
			slot = &s->cull_hyst[0];
			for(int k = 1; k < s->n_cull_hyst; k++)
				if(s->cull_hyst[k].seen_tick < slot->seen_tick)
					slot = &s->cull_hyst[k];
		}
		memset(slot, 0, sizeof(*slot));
		g_strlcpy(slot->uuid, uuid, SLV_UUID_LEN);
		slot->culled = (d > slv_spatial.cutoff_dist);   /* first sight: plain threshold, no band */
	} else if(slot->culled) {
		if(d < slv_spatial.readd_dist)
			slot->culled = FALSE;
	} else {
		if(d > slv_spatial.cutoff_dist)
			slot->culled = TRUE;
	}
	slot->seen_tick = now_tick;
	return slot->culled;
}

static void janus_slvoice_room_tick(janus_slvoice_room *room) {
	gint64 t0 = janus_get_monotonic_time();
	janus_mutex_lock(&room->mutex);
	room->tick_seq++;

	janus_slvoice_session *sess[SLV_MAX_MIX];
	const float *srcbuf[SLV_MAX_MIX];
	int audible[SLV_MAX_MIX];
	int count = 0;
	GHashTableIter it;
	gpointer v;
	g_hash_table_iter_init(&it, room->participants);
	while(g_hash_table_iter_next(&it, NULL, &v)) {
		if(count >= SLV_MAX_MIX) {
			JANUS_LOG(LOG_WARN, "[%s] room %"PRIu64" has >%d participants; mixing only the first %d\n",
				JANUS_SLVOICE_PACKAGE, room->room_id, SLV_MAX_MIX, SLV_MAX_MIX);
			break;
		}
		sess[count++] = (janus_slvoice_session *)v;
	}

	/* Pass 1: decode each source once. */
	for(int i = 0; i < count; i++) {
		janus_slvoice_session *s = sess[i];
		janus_mutex_lock(&s->mutex);
		janus_slvoice_tick_decode_locked(s, t0);
		audible[i] = (s->dec_samples > 0) ? 1 : 0;
		srcbuf[i] = s->decbuf;   /* tick-owned; stable for the rest of this tick */
		janus_slvoice_snapshot_geometry_locked(s);   /* Phase 3b: geometry + §7.1 leash (tick-owned) */
		janus_mutex_unlock(&s->mutex);
	}

	/* Pass 2: per-listener N-minus-one mix (or echo), encode, relay. */
	for(int i = 0; i < count; i++) {
		janus_slvoice_session *s = sess[i];
		if(!g_atomic_int_get(&s->webrtc_up) || !s->media_ready)
			continue;   /* no PeerConnection / no codecs -> nowhere to send */

		/* Hold s->mutex for the whole listener body: it guards this session's
		 * peer-control snapshot, echo ring, and output RTP state against a
		 * concurrent echo toggle / SLData update. Reading OTHER sessions' decbuf
		 * (srcbuf[j]) needs no lock — those are tick-owned and already written in
		 * pass 1. relay_rtp under the session lock matches the proven Phase-1
		 * path. Order is room->mutex -> session->mutex throughout. */
		int mutes[SLV_MAX_MIX];
		float gains[SLV_MAX_MIX];
		/* Phase 3b item 4: per-channel gains for the stereo (panned) mix. Same
		 * sizing and lifetime as gains[] — stack, rebuilt each tick, no allocation.
		 * They DERIVE from gains[] (which still carries peer gain * attenuation);
		 * the pan below splits that scalar into L/R, or leaves both equal to it. */
		float gainsL[SLV_MAX_MIX];
		float gainsR[SLV_MAX_MIX];
		janus_mutex_lock(&s->mutex);
		gboolean echo = g_atomic_int_get(&s->echo_active);
		for(int j = 0; j < count; j++) {
			mutes[j] = 0;
			gains[j] = 1.0f;
			gainsL[j] = 1.0f;   /* item 4: default to the same unity gains[] starts at, so */
			gainsR[j] = 1.0f;   /* a source with no geometry / no pan behaves as pre-3b. */
			if(j == i)
				continue;
			const char *disp = sess[j]->display;
			if(disp == NULL)
				continue;
			for(int k = 0; k < s->n_peer_ctl; k++) {
				if(strcmp(s->peer_ctl[k].uuid, disp) == 0) {
					/* Silence the source if the listener personally muted it OR the sim
					 * moderation-muted it (Option A). mod_muted is the ONLY new term in the
					 * mix; a moderation mute leaves the source in the roster (never in
					 * L->excluded) and is silenced here instead of by the exclusion cull. */
					if(s->peer_ctl[k].muted || s->peer_ctl[k].mod_muted)
						mutes[j] = 1;
					if(s->peer_ctl[k].has_gain)
						gains[j] = s->peer_ctl[k].gain;
					break;
				}
			}
			/* Phase 3a visibility: a sim-excluded source contributes nothing to
			 * this listener's mix. Culled here (O(1) set lookup) BEFORE any Phase-3b
			 * spatial DSP, alongside the per-source mute and the VAD/DTX cull. Reads
			 * the SAME set the roster/presence path uses (single source of truth). */
			if(slv_roster_excludes(s->excluded, disp))
				mutes[j] = 1;
			/* Phase 3b item 2: per-listener distance cull (spatial stage — after
			 * exclusion/mute/gain). Skip pairs already dropped (no distance math,
			 * no hysteresis churn) and pairs without geometry (leave in the flat
			 * mix). Writes mutes[j] and the hysteresis slot only — never the global
			 * active/audible/decode state. Attenuation is item 3; this only drops. */
			if(mutes[j])
				continue;
			/* Item 4 no-geometry fallback: both channel gains equal the scalar gain
			 * gains[j] (peer gain resolved above; attenuation cannot apply without
			 * geometry). If the snapshot is invalid the pan below is skipped and these
			 * values stand — a flat, centred, pre-3b mix. The pan overwrites them only
			 * when geometry is valid on both sides. */
			gainsL[j] = gains[j];
			gainsR[j] = gains[j];
			if(!s->snap_valid || !sess[j]->snap_valid)
				continue;
			double dcull = slv_vec3_mag(slv_vec3_sub(s->snap_lp, sess[j]->snap_sp));
			if(janus_slvoice_distance_cull_locked(s, disp, dcull, room->tick_seq)) {
				mutes[j] = 1;
			} else if(dcull > slv_spatial.ref_dist) {
				/* Phase 3b item 3: distance attenuation — the else of the cull, on the
				 * SAME dcull (no second distance). Non-culled and past the reference:
				 * normalized falloff, exactly 1 at the reference and exactly 0 at the
				 * cutoff (the cull owns d >= cutoff). Multiplies into gains[j] — which
				 * holds 1.0 or the viewer gain — never replaces. Inside the reference:
				 * full volume, no pow. Mono still; panning is item 4. */
				double t = (slv_spatial.cutoff_dist - dcull) / (slv_spatial.cutoff_dist - slv_spatial.ref_dist);
				gains[j] *= (float)pow(t, slv_spatial.falloff_exp);
			}
			/* Phase 3b item 4: constant-power azimuth pan. Runs after attenuation, on
			 * the SAME guard the cull/attenuation use (geometry valid on both sides),
			 * for sources the cull did NOT drop (mutes[j] still 0). Splits the final
			 * scalar gains[j] into L/R by the horizontal azimuth of the source in this
			 * listener's head frame; behind renders centred (Amendment 5). */
			if(!mutes[j]) {
				double az = slv_azimuth(s->snap_lp, s->snap_lh, sess[j]->snap_sp);
				float pl = 1.0f, pr = 1.0f;
				slv_pan(az, &pl, &pr);
				gainsL[j] = gains[j] * pl;
				gainsR[j] = gains[j] * pr;
			}
		}

		float frame[SLV_FRAME_TOTAL];
		int summed = 1;   /* echo relays the participant's own frame (no N-1 sum) */
		if(echo) {
			janus_slvoice_build_echo(s, frame, SLV_FRAME_SAMPLES);
		} else {
			summed = slv_mix_nminus1_stereo(frame, SLV_FRAME_TOTAL, srcbuf, audible,
				mutes, gainsL, gainsR, count, i);
			slv_mix_clamp(frame, SLV_FRAME_TOTAL);
		}
		/* RMS of the finalized output frame relayed to this listener. slv_mix_is_silent
		 * used to compute this and throw it away; compute it once via the same public
		 * slv_mix_rms and RETAIN it for query_session (last_mix_rms). The silence test
		 * below is byte-for-byte the same as slv_mix_is_silent (<= SLV_VAD_RMS), so the
		 * encode-skip decision, the threshold, the mix, and everything audible are
		 * identical. DIAGNOSTIC-ONLY — last_mix_rms drives nothing. */
		double out_rms = slv_mix_rms(frame, SLV_FRAME_TOTAL);
		s->last_mix_rms = out_rms;
		/* Phase 3b item 4: per-channel RMS of the interleaved output (even = left,
		 * odd = right). Computed inline because slv_mix_rms strides contiguously and
		 * cannot address one channel (mix.h). DIAGNOSTIC-ONLY: it reads the finished
		 * frame and feeds nothing back — out_rms, the silence test, the encode-skip
		 * decision, and everything audible below are untouched. */
		double sumsq_l = 0.0, sumsq_r = 0.0;
		for(int t = 0; t + 1 < SLV_FRAME_TOTAL; t += 2) {
			double l = (double)frame[t], r = (double)frame[t + 1];
			sumsq_l += l * l;
			sumsq_r += r * r;
		}
		s->last_mix_rms_l = sqrt(sumsq_l / (double)SLV_FRAME_SAMPLES);
		s->last_mix_rms_r = sqrt(sumsq_r / (double)SLV_FRAME_SAMPLES);
		gboolean silence = (summed == 0) || (out_rms <= SLV_VAD_RMS);
		if(!silence)
			s->frames_mixed++;
		janus_slvoice_encode_relay(s, frame, SLV_FRAME_SAMPLES, silence);
		janus_mutex_unlock(&s->mutex);
	}
	janus_mutex_unlock(&room->mutex);

	/* Tick-duration histogram (spec §4.1): buckets <5 / 5-10 / 10-15 / 15-20 / >=20 ms. */
	double ms = (double)(janus_get_monotonic_time() - t0) / 1000.0;
	int b = ms < 5.0 ? 0 : ms < 10.0 ? 1 : ms < 15.0 ? 2 : ms < 20.0 ? 3 : 4;
	janus_mutex_lock(&room->mutex);
	room->tick_last_ms = ms;
	if(ms > room->tick_max_ms)
		room->tick_max_ms = ms;
	room->tick_hist[b]++;
	if(ms > SLV_TICK_WARN_MS)
		room->tick_overruns++;
	guint64 seq = room->tick_seq;
	janus_mutex_unlock(&room->mutex);
	if(ms > SLV_TICK_WARN_MS)
		JANUS_LOG(LOG_WARN, "[%s] room %"PRIu64" tick %"PRIu64" took %.1fms (>%.0fms deadline)\n",
			JANUS_SLVOICE_PACKAGE, room->room_id, seq, ms, SLV_TICK_WARN_MS);
}

/* The per-room 20ms tick thread. Holds a room ref for its lifetime. */
static gpointer janus_slvoice_room_ticker(gpointer data) {
	janus_slvoice_room *room = (janus_slvoice_room *)data;
	JANUS_LOG(LOG_VERB, "[%s] Tick thread up for room %"PRIu64"\n", JANUS_SLVOICE_PACKAGE, room->room_id);
	gint64 next = janus_get_monotonic_time();
	while(g_atomic_int_get(&room->tick_running) && !g_atomic_int_get(&stopping)) {
		janus_slvoice_room_tick(room);
		next += (gint64)SLV_TICK_MS * 1000;
		gint64 now = janus_get_monotonic_time();
		if(next > now)
			g_usleep((gulong)(next - now));
		else
			next = now;   /* fell behind: reset the schedule rather than bursting */
	}
	JANUS_LOG(LOG_VERB, "[%s] Tick thread down for room %"PRIu64"\n", JANUS_SLVOICE_PACKAGE, room->room_id);
	janus_refcount_decrease(&room->ref);
	return NULL;
}

static gboolean janus_slvoice_room_start(janus_slvoice_room *room) {
	if(room == NULL || room->ticker != NULL)
		return room != NULL;
	g_atomic_int_set(&room->tick_running, 1);
	janus_refcount_increase(&room->ref);   /* the ticker holds a ref while alive */
	GError *err = NULL;
	char tname[24];
	g_snprintf(tname, sizeof(tname), "slv tick %u", (unsigned)(room->room_id & 0xffff));
	room->ticker = g_thread_try_new(tname, janus_slvoice_room_ticker, room, &err);
	if(err != NULL) {
		JANUS_LOG(LOG_ERR, "[%s] Could not start tick thread for room %"PRIu64": %s\n",
			JANUS_SLVOICE_PACKAGE, room->room_id, err->message ? err->message : "??");
		g_error_free(err);
		g_atomic_int_set(&room->tick_running, 0);
		janus_refcount_decrease(&room->ref);
		room->ticker = NULL;
		return FALSE;
	}
	return TRUE;
}

static void janus_slvoice_room_stop(janus_slvoice_room *room) {
	if(room == NULL || room->ticker == NULL)
		return;
	g_atomic_int_set(&room->tick_running, 0);
	g_thread_join(room->ticker);
	room->ticker = NULL;
}

/* Merge a parsed per-source SLData control payload into this listener's
 * persistent peer-control map. session->mutex held. */
static void janus_slvoice_apply_peer_ctl_locked(janus_slvoice_session *s, const slv_sldata *d) {
	for(int i = 0; i < d->n_peers; i++) {
		const slv_peer_adj *a = &d->peers[i];
		slv_peer_ctl *dst = NULL;
		for(int k = 0; k < s->n_peer_ctl; k++) {
			if(strcmp(s->peer_ctl[k].uuid, a->uuid) == 0) {
				dst = &s->peer_ctl[k];
				break;
			}
		}
		if(dst == NULL) {
			if(s->n_peer_ctl >= SLV_MAX_PEER_ADJ)
				continue;   /* table full: drop extras */
			dst = &s->peer_ctl[s->n_peer_ctl++];
			memset(dst, 0, sizeof(*dst));
			g_strlcpy(dst->uuid, a->uuid, SLV_UUID_LEN);
		}
		if(a->has_mute)
			dst->muted = a->muted ? TRUE : FALSE;
		if(a->has_gain) {
			double g = a->gain / SLV_GAIN_FACTOR;   /* ug is volume*220 */
			if(g < 0.0) g = 0.0;
			else if(g > SLV_GAIN_MAX) g = SLV_GAIN_MAX;
			dst->has_gain = TRUE;
			dst->gain = (float)g;
		}
	}
}

/* ---- Media callbacks ----------------------------------------------------- */

void janus_slvoice_setup_media(janus_plugin_session *handle) {
	janus_slvoice_session *session = (janus_slvoice_session *)handle->plugin_handle;
	if(session == NULL)
		return;
	g_atomic_int_set(&session->webrtc_up, 1);
	JANUS_LOG(LOG_INFO, "[%s-%p] WebRTC media is now available (ICE connected, DTLS complete, PeerConnection up)\n",
		JANUS_SLVOICE_PACKAGE, handle);
	if(echo_autostart) {
		janus_mutex_lock(&session->mutex);
		janus_slvoice_echo_start_locked(session);
		gboolean ok = g_atomic_int_get(&session->echo_active);
		janus_mutex_unlock(&session->mutex);
		JANUS_LOG(ok ? LOG_INFO : LOG_ERR, "[%s-%p] Echo %s (SLV_ECHO_AUTOSTART; hears self instead of the mix)\n",
			JANUS_SLVOICE_PACKAGE, handle, ok ? "auto-started" : "auto-start FAILED");
	}
}

void janus_slvoice_incoming_rtp(janus_plugin_session *handle, janus_plugin_rtp *packet) {
	if(g_atomic_int_get(&stopping) || !g_atomic_int_get(&initialized))
		return;
	janus_slvoice_session *session = (janus_slvoice_session *)handle->plugin_handle;
	if(session == NULL || g_atomic_int_get(&session->destroyed))
		return;
	if(packet == NULL || packet->buffer == NULL || packet->video)
		return;   /* audio only */
	/* Phase 2: incoming_rtp only feeds the jitter buffer and stamps liveness for
	 * the DTX/VAD cull. Decode + mix + relay happen on the room's 20ms tick, so
	 * every participant is decoded once on a common clock and mixed. No decode
	 * or allocation here. */
	int plen = 0;
	unsigned char *payload = (unsigned char *)janus_rtp_payload(packet->buffer, packet->length, &plen);
	janus_rtp_header *rtp = (janus_rtp_header *)packet->buffer;
	uint16_t seq = ntohs(rtp->seq_number);
	janus_mutex_lock(&session->mutex);
	session->rtp_in_count++;
	session->last_rtp_us = janus_get_monotonic_time();   /* keeps the talker in the active set */
	if(session->media_ready && payload != NULL && plen > 0)
		janus_slvoice_jb_insert(session, seq, payload, plen);
	janus_mutex_unlock(&session->mutex);
	SLV_MEDIA_LOG("[%s-%p] incoming_rtp seq=%u %d payload bytes -> jitter buffer\n",
		JANUS_SLVOICE_PACKAGE, handle, seq, plen);
}

void janus_slvoice_incoming_rtcp(janus_plugin_session *handle, janus_plugin_rtcp *packet) {
	/* Phase 1A: RTCP is not acted on. */
}

void janus_slvoice_incoming_data(janus_plugin_session *handle, janus_plugin_data *packet) {
	if(g_atomic_int_get(&stopping) || !g_atomic_int_get(&initialized))
		return;
	janus_slvoice_session *session = (janus_slvoice_session *)handle->plugin_handle;
	if(session == NULL || g_atomic_int_get(&session->destroyed))
		return;
	if(packet == NULL || packet->buffer == NULL || packet->length == 0)
		return;

	slv_sldata d;
	slv_sldata_status st = slv_sldata_parse(packet->buffer, packet->length, &d);

	janus_mutex_lock(&session->mutex);
	session->data_msgs_received++;
	if(st == SLV_SLDATA_OK || st == SLV_SLDATA_EMPTY) {
		session->last_data = d;
		session->last_data_fields = d.fields_seen;
		/* Per-source mute/gain the viewer set on OTHER participants
		 * ({"m":{uuid:bool}} / {"ug":{uuid:val}}) — merge into the persistent
		 * per-listener control map the tick reads (req 4). */
		if(d.n_peers > 0)
			janus_slvoice_apply_peer_ctl_locked(session, &d);
	}
	janus_mutex_unlock(&session->mutex);

	if(st == SLV_SLDATA_MALFORMED) {
		JANUS_LOG(LOG_WARN, "[%s-%p] Malformed SLData on data channel (%u bytes); ignored\n",
			JANUS_SLVOICE_PACKAGE, handle, packet->length);
		return;
	}
	if(st == SLV_SLDATA_TOOBIG) {
		JANUS_LOG(LOG_WARN, "[%s-%p] Oversized SLData on data channel (%u bytes); ignored\n",
			JANUS_SLVOICE_PACKAGE, handle, packet->length);
		return;
	}

	/* Echo toggle (slvoice extension): {"echo":true} / {"echo":false} */
	if(d.fields_seen & SLV_FIELD_ECHO) {
		janus_mutex_lock(&session->mutex);
		if(d.echo) {
			janus_slvoice_echo_start_locked(session);
			gboolean ok = g_atomic_int_get(&session->echo_active);
			JANUS_LOG(ok ? LOG_INFO : LOG_ERR, "[%s-%p] Echo %s (SLData toggle; hears self instead of the mix)\n",
				JANUS_SLVOICE_PACKAGE, handle, ok ? "ENABLED (500ms delay)" : "enable FAILED");
		} else {
			janus_slvoice_echo_stop_locked(session);
			JANUS_LOG(LOG_INFO, "[%s-%p] Echo DISABLED (SLData toggle)\n", JANUS_SLVOICE_PACKAGE, handle);
		}
		janus_mutex_unlock(&session->mutex);
	}
#ifdef SLV_DEBUG_MEDIA
	{
		char fbuf[96];
		slv_sldata_fields_str(d.fields_seen, fbuf, sizeof(fbuf));
		SLV_MEDIA_LOG("[%s-%p] SLData fields=[%s] (status=%d)\n", JANUS_SLVOICE_PACKAGE, handle, fbuf, st);
	}
#endif
	/* Phase 1A stores sp/sh/lp/lh/m/ug (in session->last_data) but does not act
	 * on the geometry — spatial mixing is Phase 2. */
}

void janus_slvoice_data_ready(janus_plugin_session *handle) {
	janus_slvoice_session *session = (janus_slvoice_session *)handle->plugin_handle;
	if(session == NULL)
		return;
	/* Do not relay_data before this fires (janus_textroom.c:1491). */
	/* M-A2A-1 PRESENCE FENCE (O-42b): the dc_open flip and the join-backlog snapshot
	 * are ONE critical section under room->mutex, and push_presence's per-recipient
	 * dc_open read (inside relay_json) already runs under the SAME mutex. So for every
	 * (joiner J, recipient R) pair, order the three room->mutex sections — I = J's
	 * participant insert, P = the push to R (reads R->dc_open), F = THIS fence
	 * (R->dc_open := 1, then snapshot; I precedes P by program order):
	 *   F < I < P : snapshot misses J, but P reads dc_open==1 -> the push delivers.
	 *   I < F < P : snapshot includes J -> the backlog delivers (P may deliver too).
	 *   I < P < F : P reads dc_open==0 and drops, but the snapshot includes J -> the
	 *               backlog delivers.
	 * At least one of {push, backlog} delivers in every ordering, by mutual exclusion
	 * alone — no memory-model reasoning required. The I<F<P duplicate is benign and
	 * pre-existing: a second "j" is idempotent at the viewer (addParticipantByID by
	 * UUID; documented above the backlog). Without the fence the same conclusion held
	 * only via glib's atomic barriers; a drop in relay_json now provably cannot be
	 * the join race, which is what makes presence_dropped_dc_closed meaningful.
	 * A session with no room yet (data channel up before any join) just flips the
	 * flag — there is nothing to snapshot, and its later join's push path (or its
	 * re-entry here, which the CAS prevents) is not needed: every subsequent joiner's
	 * push will find dc_open==1. Lock order room->mutex -> session->mutex preserved:
	 * the room is resolved under session->mutex FIRST and released before locking. */
	janus_slvoice_room *room = NULL;
	janus_mutex_lock(&session->mutex);
	if(session->room != NULL && !g_atomic_int_get(&session->room->destroyed)) {
		room = session->room;
		janus_refcount_increase(&room->ref);   /* macro expands to a block: braces required */
	}
	janus_mutex_unlock(&session->mutex);
	if(room != NULL)
		janus_mutex_lock(&room->mutex);
	if(g_atomic_int_compare_and_exchange(&session->dc_open, 0, 1)) {
		JANUS_LOG(LOG_INFO, "[%s-%p] Data channel open (writable)\n", JANUS_SLVOICE_PACKAGE, handle);
		/* First time writable: send the roster backlog so occupants already present
		 * when this listener joined get a row now, not on the next transition
		 * (docs/join-backlog-defect.md). The CAS above makes this fire exactly once. */
		if(room != NULL)
			janus_slvoice_send_join_backlog_locked(session, room);
	}
	if(room != NULL) {
		janus_mutex_unlock(&room->mutex);
		janus_refcount_decrease(&room->ref);
	}
}

void janus_slvoice_slow_link(janus_plugin_session *handle, int mindex, gboolean video, gboolean uplink) {
	JANUS_LOG(LOG_VERB, "[%s-%p] slow_link: mindex=%d, %s, %s (informational)\n",
		JANUS_SLVOICE_PACKAGE, handle, mindex, video ? "video" : "audio", uplink ? "uplink" : "downlink");
}

void janus_slvoice_hangup_media(janus_plugin_session *handle) {
	JANUS_LOG(LOG_INFO, "[%s-%p] WebRTC media is gone (PeerConnection down)\n", JANUS_SLVOICE_PACKAGE, handle);
	if(g_atomic_int_get(&stopping) || !g_atomic_int_get(&initialized))
		return;
	janus_slvoice_session *session = (janus_slvoice_session *)handle->plugin_handle;
	if(session == NULL)
		return;
	g_atomic_int_set(&session->webrtc_up, 0);
	g_atomic_int_set(&session->dc_open, 0);
	janus_mutex_lock(&session->mutex);
	janus_slvoice_echo_stop_locked(session);
	janus_mutex_unlock(&session->mutex);
}
