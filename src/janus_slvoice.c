/*! \file    janus_slvoice.c
 * \author   Legion Voice Mixer project
 * \copyright GNU General Public License v3
 * \brief    Janus SLVoice plugin — Phase 1 (echo test)
 *
 * \details  \c janus.plugin.slvoice is a spatial voice mixer for OpenSimulator
 * grids speaking the Second Life WebRTC voice protocol. This is the Phase-1
 * build: it proves the full single-participant media path (JSEP negotiation ->
 * ICE/DTLS -> Opus RTP in -> decode -> 500ms delay -> Opus encode -> RTP back
 * to the same peer) WITHOUT any mixing, spatial, or per-listener logic. Those
 * are Phase 2 (see src/mixer/mixer.h).
 *
 * Protocol: handle_message() implements a superset of the audiobridge request
 * protocol the OpenSim C# side already emits (create/destroy/join/leave/
 * configure/list/listparticipants), with the SAME field names and the SAME
 * response envelope key ("audiobridge") so the C# side can flip between
 * janus.plugin.audiobridge and janus.plugin.slvoice by configuration alone,
 * with no code change (A/B fault isolation). See docs/protocol-compat.md.
 *
 * Echo mode is toggled per-participant by an "echo" field on the data-channel
 * SLData payload (a slvoice extension; see docs/sldata-extensions.md), not by a
 * plugin request.
 *
 * Written against the Janus 1.4.1 plugin API (vendor/janus-gateway,
 * JANUS_PLUGIN_API_VERSION 106); the vendored headers and in-tree plugins
 * (audiobridge, echotest) are the authority, not remembered signatures.
 */

#include <inttypes.h>
#include <string.h>
#include <arpa/inet.h>

#include <opus/opus.h>
#include <jansson.h>

#include <janus/plugins/plugin.h>
#include <janus/debug.h>
#include <janus/config.h>
#include <janus/mutex.h>
#include <janus/refcount.h>
#include <janus/utils.h>
#include <janus/rtp.h>
#include <janus/sdp-utils.h>

#include "sldata.h"

/* Plugin information */
#define JANUS_SLVOICE_VERSION         2
#define JANUS_SLVOICE_VERSION_STRING  "0.2.0"
#define JANUS_SLVOICE_DESCRIPTION     "Spatial voice mixer for OpenSimulator, speaking the Second Life WebRTC voice protocol (Phase 1: single-participant echo test; no mixing yet)."
#define JANUS_SLVOICE_NAME            "Legion SLVoice mixer"
#define JANUS_SLVOICE_AUTHOR          "Legion Voice Mixer project"
#define JANUS_SLVOICE_PACKAGE         "janus.plugin.slvoice"

/* Application-level error codes (returned in the audiobridge-shaped error
 * envelope: {"audiobridge":"event","error_code":N,"error":"..."}).
 *
 * These MUST match janus.plugin.audiobridge's numeric codes: the OpenSim C#
 * side reads the `error_code` field and, in particular,
 * JanusAudioBridge.CreateRoom treats 486 ("room exists") as success
 * (docs/voice/current-architecture.md §3.3). Numbers taken verbatim from
 * vendor/janus-gateway/src/plugins/janus_audiobridge.c. */
#define JANUS_SLVOICE_ERROR_NO_MESSAGE      480
#define JANUS_SLVOICE_ERROR_INVALID_JSON    481
#define JANUS_SLVOICE_ERROR_INVALID_REQUEST 482
#define JANUS_SLVOICE_ERROR_MISSING_ELEMENT 483
#define JANUS_SLVOICE_ERROR_INVALID_ELEMENT 484
#define JANUS_SLVOICE_ERROR_NO_SUCH_ROOM    485
#define JANUS_SLVOICE_ERROR_ROOM_EXISTS     486
#define JANUS_SLVOICE_ERROR_NOT_JOINED      487
#define JANUS_SLVOICE_ERROR_UNAUTHORIZED    489
#define JANUS_SLVOICE_ERROR_ALREADY_JOINED  491
#define JANUS_SLVOICE_ERROR_INVALID_SDP     493
#define JANUS_SLVOICE_ERROR_UNKNOWN         499

/* ---- Media constants (Phase 1 echo) --------------------------------------
 * Everything runs at Opus fullband, stereo (matching the negotiated
 * stereo=1;sprop-stereo=1 fmtp), so decode/encode channel counts are fixed. */
#define SLV_RATE            48000
#define SLV_CHANNELS        2
#define SLV_ECHO_DELAY_MS   500
#define SLV_DELAY_SAMPLES   ((SLV_RATE/1000)*SLV_ECHO_DELAY_MS)   /* 24000 per channel */
#define SLV_DECODE_MAX      5760    /* per-channel decode headroom (120ms @48k = max Opus frame) */
#define SLV_RING_SAMPLES    (SLV_DELAY_SAMPLES + SLV_DECODE_MAX + (SLV_RATE/1000)*20)
#define SLV_RTP_OUT_MAX     1500

/* Packet-level media logging is gated behind a compile-time flag so the hot
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
static GThread *handler_thread = NULL;
static void *janus_slvoice_handler(void *data);

/* Phase-1 bring-up convenience: when set, echo mode is auto-enabled as soon as
 * a participant's PeerConnection comes up, so a stock viewer hears itself
 * without having to send an {"echo":true} SLData message. Default OFF. Set via
 * the SLV_ECHO_AUTOSTART environment variable or general.echo_autostart in the
 * plugin jcfg. The {"echo":true}/{"echo":false} data-channel toggle still works
 * regardless. See docs/phase1-bringup.md and docs/sldata-extensions.md. */
static gboolean echo_autostart = FALSE;

/* ---- Rooms (slv_regions) and participants (folded into the session) -------
 * Phase 1 keeps the participant state on the session itself: one WebRTC peer =
 * one participant. A room is a lightweight membership + metadata holder; the
 * per-region mix thread (src/mixer/mixer.h) is Phase 2. */
typedef struct janus_slvoice_room {
	guint64 room_id;
	char *description;
	gboolean is_private;
	gboolean permanent;         /* loaded from static config (not saved on destroy) */
	guint32 sampling_rate;      /* advertised in list; internal mix rate is fixed 48k */
	gboolean spatial_audio;
	GHashTable *participants;    /* guint64* user_id -> janus_slvoice_session* (borrowed) */
	janus_mutex mutex;
	volatile gint destroyed;
	janus_refcount ref;
} janus_slvoice_room;

typedef struct janus_slvoice_session {
	janus_plugin_session *handle;
	guint64 user_id;             /* participant id; 0 until joined */
	janus_slvoice_room *room;    /* ref held while joined; NULL otherwise (guarded by mutex) */
	char *display;               /* display name from join */
	int opus_pt;                 /* negotiated Opus payload type; -1 until join */
	gboolean has_datachannel;    /* data channel present in the negotiated SDP */

	volatile gint webrtc_up;     /* setup_media(1) / hangup_media(0) — coarse ICE/DTLS up */
	volatile gint dc_open;       /* data_ready seen at least once */

	/* Echo/media state (guarded by mutex). All buffers allocated at echo-enable
	 * and freed at echo-disable — no per-packet allocation. */
	volatile gint echo_active;
	OpusDecoder *dec;
	OpusEncoder *enc;
	float *ring;                 /* SLV_RING_SAMPLES * SLV_CHANNELS interleaved floats */
	int ring_wpos;               /* write cursor, per-channel sample index */
	float *decbuf;               /* SLV_DECODE_MAX * SLV_CHANNELS interleaved floats */
	float *framebuf;             /* SLV_DECODE_MAX * SLV_CHANNELS delayed frame to encode */
	unsigned char *outbuf;       /* SLV_RTP_OUT_MAX: 12B RTP header + Opus payload */
	uint16_t out_seq;
	uint32_t out_ts;
	uint32_t out_ssrc;

	/* Diagnostics (guarded by mutex except where atomic) */
	guint64 rtp_in_count;
	guint64 rtp_in_bytes;
	gint64  rate_t0;             /* start of the current 1s rate window (monotonic us) */
	guint32 rate_ctr;            /* packets in the current window */
	guint32 rtp_in_rate;         /* packets in the last completed 1s window */
	volatile gint last_decode_ok;/* 1 ok, 0 fail, -1 none yet */
	guint64 data_msgs_received;
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

/* ---- Room helpers -------------------------------------------------------- */

static void janus_slvoice_room_free(const janus_refcount *ref) {
	janus_slvoice_room *room = janus_refcount_containerof(ref, janus_slvoice_room, ref);
	JANUS_LOG(LOG_VERB, "[%s] Freeing room %"PRIu64"\n", JANUS_SLVOICE_PACKAGE, room->room_id);
	if(room->participants)
		g_hash_table_destroy(room->participants);
	g_free(room->description);
	g_free(room);
}

static janus_slvoice_room *janus_slvoice_room_create(guint64 id, const char *desc,
		gboolean is_private, guint32 rate, gboolean spatial, gboolean permanent) {
	janus_slvoice_room *room = g_malloc0(sizeof(janus_slvoice_room));
	room->room_id = id;
	room->description = desc ? g_strdup(desc) : g_strdup_printf("Region %"PRIu64, id);
	room->is_private = is_private;
	room->sampling_rate = rate ? rate : SLV_RATE;
	room->spatial_audio = spatial;
	room->permanent = permanent;
	/* keys are heap guint64 (freed by the table); values are borrowed sessions */
	room->participants = g_hash_table_new_full(g_int64_hash, g_int64_equal, g_free, NULL);
	janus_mutex_init(&room->mutex);
	janus_refcount_init(&room->ref, janus_slvoice_room_free);
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

/* ---- Echo resource management (session->mutex must be held) --------------- */

static void janus_slvoice_echo_free_locked(janus_slvoice_session *s) {
	if(s->dec) { opus_decoder_destroy(s->dec); s->dec = NULL; }
	if(s->enc) { opus_encoder_destroy(s->enc); s->enc = NULL; }
	g_free(s->ring);      s->ring = NULL;
	g_free(s->decbuf);    s->decbuf = NULL;
	g_free(s->framebuf);  s->framebuf = NULL;
	g_free(s->outbuf);    s->outbuf = NULL;
	s->ring_wpos = 0;
}

static gboolean janus_slvoice_echo_start_locked(janus_slvoice_session *s) {
	if(g_atomic_int_get(&s->echo_active) && s->dec && s->enc)
		return TRUE;
	int err = 0;
	s->dec = opus_decoder_create(SLV_RATE, SLV_CHANNELS, &err);
	if(err != OPUS_OK || s->dec == NULL) {
		JANUS_LOG(LOG_ERR, "[%s] opus_decoder_create failed: %s\n", JANUS_SLVOICE_PACKAGE, opus_strerror(err));
		janus_slvoice_echo_free_locked(s);
		return FALSE;
	}
	err = 0;
	s->enc = opus_encoder_create(SLV_RATE, SLV_CHANNELS, OPUS_APPLICATION_VOIP, &err);
	if(err != OPUS_OK || s->enc == NULL) {
		JANUS_LOG(LOG_ERR, "[%s] opus_encoder_create failed: %s\n", JANUS_SLVOICE_PACKAGE, opus_strerror(err));
		janus_slvoice_echo_free_locked(s);
		return FALSE;
	}
	opus_encoder_ctl(s->enc, OPUS_SET_MAX_BANDWIDTH(OPUS_BANDWIDTH_FULLBAND));
	opus_encoder_ctl(s->enc, OPUS_SET_INBAND_FEC(1));
	opus_encoder_ctl(s->enc, OPUS_SET_PACKET_LOSS_PERC(10));
	opus_encoder_ctl(s->enc, OPUS_SET_COMPLEXITY(9));
	opus_encoder_ctl(s->enc, OPUS_SET_BITRATE(48000));
	s->ring     = g_malloc0(sizeof(float) * SLV_RING_SAMPLES * SLV_CHANNELS);
	s->decbuf   = g_malloc0(sizeof(float) * SLV_DECODE_MAX * SLV_CHANNELS);
	s->framebuf = g_malloc0(sizeof(float) * SLV_DECODE_MAX * SLV_CHANNELS);
	s->outbuf   = g_malloc0(SLV_RTP_OUT_MAX);
	s->ring_wpos = 0;
	s->out_seq  = (uint16_t)(janus_random_uint64() & 0xFFFF);
	s->out_ts   = (uint32_t)(janus_random_uint64() & 0xFFFFFFFF);
	s->out_ssrc = (uint32_t)(janus_random_uint64() & 0xFFFFFFFF);
	g_atomic_int_set(&s->echo_active, 1);
	return TRUE;
}

static void janus_slvoice_echo_stop_locked(janus_slvoice_session *s) {
	if(!g_atomic_int_get(&s->echo_active) && s->dec == NULL && s->enc == NULL)
		return;
	g_atomic_int_set(&s->echo_active, 0);
	janus_slvoice_echo_free_locked(s);
}

/* Remove a session from its room (if any) and drop the room ref. The session
 * that transitions room from non-NULL to NULL is the one that unrefs — this
 * makes leave / destroy_session / room-destroy races safe. */
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
	janus_refcount_decrease(&room->ref);
}

/* ---- Session helpers ----------------------------------------------------- */

static void janus_slvoice_session_free(const janus_refcount *ref) {
	janus_slvoice_session *session = janus_refcount_containerof(ref, janus_slvoice_session, ref);
	JANUS_LOG(LOG_VERB, "[%s] Freeing session %p\n", JANUS_SLVOICE_PACKAGE, session);
	/* Echo buffers must already be freed via echo_stop; free the rest. */
	janus_slvoice_echo_free_locked(session);
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
		guint32 rate = rate_s ? (guint32)g_ascii_strtoull(rate_s, NULL, 10) : SLV_RATE;
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
	 * SLV_ECHO_AUTOSTART environment variable (which is the convenient knob
	 * under the env-driven Docker model — just add it to .env). */
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
		JANUS_LOG(LOG_INFO, "[%s] echo_autostart ENABLED — echo starts automatically on connect\n",
			JANUS_SLVOICE_PACKAGE);

	if(config != NULL)
		janus_config_destroy(config);

	gateway = callback;
	g_atomic_int_set(&initialized, 1);

	GError *error = NULL;
	handler_thread = g_thread_try_new("slvoice handler", janus_slvoice_handler, NULL, &error);
	if(error != NULL) {
		g_atomic_int_set(&initialized, 0);
		JANUS_LOG(LOG_ERR, "[%s] Got error %d (%s) trying to launch the handler thread\n",
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

	janus_mutex_lock(&sessions_mutex);
	if(sessions != NULL) {
		g_hash_table_destroy(sessions);
		sessions = NULL;
	}
	janus_mutex_unlock(&sessions_mutex);

	janus_mutex_lock(&rooms_mutex);
	if(rooms != NULL) {
		/* Values are ref-counted rooms; drop our (hash) reference to each */
		GHashTableIter iter;
		gpointer value;
		g_hash_table_iter_init(&iter, rooms);
		while(g_hash_table_iter_next(&iter, NULL, &value)) {
			janus_slvoice_room *room = value;
			g_atomic_int_set(&room->destroyed, 1);
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
	g_atomic_int_set(&session->last_decode_ok, -1);
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
	/* Tear down media and room membership before dropping the session */
	janus_mutex_lock(&session->mutex);
	janus_slvoice_echo_stop_locked(session);
	janus_mutex_unlock(&session->mutex);
	janus_slvoice_leave_room(session);

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
	/* Plugins only learn PeerConnection up/down (setup_media / hangup_media);
	 * we report ICE/DTLS coarsely as Janus surfaces it to us. */
	json_object_set_new(info, "webrtc_up", up ? json_true() : json_false());
	json_object_set_new(info, "ice_state", json_string(up ? "connected" : "disconnected"));
	json_object_set_new(info, "dtls_state", json_string(up ? "connected" : "disconnected"));
	json_object_set_new(info, "datachannel_open", g_atomic_int_get(&session->dc_open) ? json_true() : json_false());
	json_object_set_new(info, "datachannel_negotiated", session->has_datachannel ? json_true() : json_false());
	if(session->room != NULL)
		json_object_set_new(info, "room", json_integer((json_int_t)session->room->room_id));
	json_object_set_new(info, "id", json_integer((json_int_t)session->user_id));
	json_object_set_new(info, "opus_pt", json_integer(session->opus_pt));
	json_object_set_new(info, "echo_active", g_atomic_int_get(&session->echo_active) ? json_true() : json_false());
	json_object_set_new(info, "rtp_in_count", json_integer((json_int_t)session->rtp_in_count));
	json_object_set_new(info, "rtp_in_bytes", json_integer((json_int_t)session->rtp_in_bytes));
	json_object_set_new(info, "rtp_in_rate", json_integer(session->rtp_in_rate));
	int ldo = g_atomic_int_get(&session->last_decode_ok);
	json_object_set_new(info, "last_decode_ok", ldo < 0 ? json_null() : (ldo ? json_true() : json_false()));
	json_object_set_new(info, "data_msgs_received", json_integer((json_int_t)session->data_msgs_received));
	char fbuf[96];
	slv_sldata_fields_str(session->last_data_fields, fbuf, sizeof(fbuf));
	json_object_set_new(info, "last_data_fields_seen", json_string(fbuf));
	janus_mutex_unlock(&session->mutex);
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

json_t *janus_slvoice_handle_admin_message(json_t *message) {
	const char *request_text = NULL;
	if(message != NULL && json_is_object(message)) {
		json_t *request = json_object_get(message, "request");
		if(json_is_string(request))
			request_text = json_string_value(request);
	}
	JANUS_LOG(LOG_INFO, "[%s] handle_admin_message: request=\"%s\"\n",
		JANUS_SLVOICE_PACKAGE, request_text ? request_text : "(none)");
	json_t *response = json_object();
	json_object_set_new(response, "slvoice", json_string("ack"));
	return response;
}

/* ---- JSEP negotiation helper (join / configure with an offer) ------------
 * Parses the offer, picks the Opus PT, builds an Opus (48k stereo) + optional
 * datachannel answer with the spec fmtp, and returns the answer SDP string.
 * On success returns TRUE and *answer_sdp (caller g_free's). */
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

	/* Build the answer: accept Opus audio sendrecv, accept the datachannel if
	 * present, reject everything else (generate_answer defaults to rejected). */
	janus_sdp *answer = janus_sdp_generate_answer(offer);
	GList *temp = offer->m_lines;
	while(temp != NULL) {
		janus_sdp_mline *m = (janus_sdp_mline *)temp->data;
		if(m->type == JANUS_SDP_AUDIO) {
			janus_sdp_generate_answer_mline(offer, answer, m,
				JANUS_SDP_OA_MLINE, JANUS_SDP_AUDIO,
				JANUS_SDP_OA_CODEC, "opus",
				JANUS_SDP_OA_DIRECTION, JANUS_SDP_SENDRECV,
				JANUS_SDP_OA_DONE);
		} else if(m->type == JANUS_SDP_APPLICATION) {
			janus_sdp_generate_answer_mline(offer, answer, m,
				JANUS_SDP_OA_MLINE, JANUS_SDP_APPLICATION,
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
	janus_mutex_unlock(&session->mutex);
	*answer_sdp = new_sdp;
	JANUS_LOG(LOG_INFO, "[%s-%p] Negotiated Opus pt=%d, datachannel=%s\n",
		JANUS_SLVOICE_PACKAGE, session->handle, opus_pt, has_dc ? "yes" : "no");
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
			guint32 rate = (rate_j && json_is_integer(rate_j)) ? (guint32)json_integer_value(rate_j) : SLV_RATE;
			gboolean permanent = json_is_true(json_object_get(root, "permanent"));

			janus_mutex_lock(&rooms_mutex);
			janus_slvoice_room *room = g_hash_table_lookup(rooms, &room_id);
			if(room != NULL) {
				/* Match audiobridge exactly: an existing room is error 486
				 * ("room exists"). The OpenSim C# side (CreateRoom) treats 486
				 * as success — every region that hashes to the same room number
				 * relies on this (docs/voice/current-architecture.md §3.3). */
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
			/* Evict participants: each session that we transition out of the
			 * room drops the ref it held (mutex makes the transition unique). */
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
					janus_slvoice_echo_stop_locked(p);
				}
				janus_mutex_unlock(&p->mutex);
				if(unref)
					janus_refcount_decrease(&room->ref);
			}
			g_list_free(members);
			g_hash_table_remove_all(room->participants);
			janus_mutex_unlock(&room->mutex);
			/* Drop the hash's reference */
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

			/* Already in a room? */
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
			/* Need an offer to negotiate */
			if(msg->jsep == NULL) {
				janus_refcount_decrease(&room->ref);
				error_code = JANUS_SLVOICE_ERROR_INVALID_SDP;
				g_snprintf(error_cause, 512, "join requires a JSEP offer");
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
			janus_mutex_unlock(&session->mutex);
			janus_mutex_lock(&room->mutex);
			guint64 *key = g_malloc(sizeof(guint64));
			*key = user_id;
			g_hash_table_insert(room->participants, key, session);
			/* Build the roster of OTHER participants for the joiner */
			json_t *list = json_array();
			GHashTableIter iter;
			gpointer value;
			g_hash_table_iter_init(&iter, room->participants);
			while(g_hash_table_iter_next(&iter, NULL, &value)) {
				janus_slvoice_session *p = value;
				if(p == session)
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
			JANUS_LOG(LOG_INFO, "[%s-%p] Participant %"PRIu64" (%s) joined room %"PRIu64"\n",
				JANUS_SLVOICE_PACKAGE, msg->handle, user_id, display ? display : "??", room_id);
			goto respond;
		} else if(!strcasecmp(request_text, "configure")) {
			/* Accept and ack; renegotiate if an offer rides along. */
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
			janus_mutex_unlock(&session->mutex);
			if(!joined) {
				error_code = JANUS_SLVOICE_ERROR_NOT_JOINED;
				g_snprintf(error_cause, 512, "Not in a room");
				goto respond;
			}
			janus_mutex_lock(&session->mutex);
			janus_slvoice_echo_stop_locked(session);
			janus_mutex_unlock(&session->mutex);
			janus_slvoice_leave_room(session);

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
		janus_slvoice_message_free(msg);
		msg = NULL;
	}
	JANUS_LOG(LOG_VERB, "[%s] Leaving handler thread\n", JANUS_SLVOICE_PACKAGE);
	return NULL;
}

/* ---- Media callbacks ----------------------------------------------------- */

void janus_slvoice_setup_media(janus_plugin_session *handle) {
	janus_slvoice_session *session = (janus_slvoice_session *)handle->plugin_handle;
	if(session == NULL)
		return;
	g_atomic_int_set(&session->webrtc_up, 1);
	JANUS_LOG(LOG_INFO, "[%s-%p] WebRTC media is now available (PeerConnection up)\n",
		JANUS_SLVOICE_PACKAGE, handle);
	if(echo_autostart) {
		janus_mutex_lock(&session->mutex);
		gboolean ok = janus_slvoice_echo_start_locked(session);
		janus_mutex_unlock(&session->mutex);
		JANUS_LOG(ok ? LOG_INFO : LOG_ERR, "[%s-%p] Echo %s (echo_autostart)\n",
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
		return;   /* audio only in Phase 1 */

	janus_mutex_lock(&session->mutex);
	/* Diagnostics counters + 1s rate window */
	gint64 now = janus_get_monotonic_time();
	session->rtp_in_count++;
	session->rtp_in_bytes += packet->length;
	if(session->rate_t0 == 0)
		session->rate_t0 = now;
	session->rate_ctr++;
	if(now - session->rate_t0 >= G_USEC_PER_SEC) {
		session->rtp_in_rate = session->rate_ctr;
		session->rate_ctr = 0;
		session->rate_t0 = now;
	}

	if(!g_atomic_int_get(&session->echo_active) || session->dec == NULL || session->enc == NULL) {
		janus_mutex_unlock(&session->mutex);
		SLV_MEDIA_LOG("[%s-%p] incoming_rtp %d bytes (echo off; dropped)\n",
			JANUS_SLVOICE_PACKAGE, handle, packet->length);
		return;
	}

	/* Decode Opus -> interleaved 48k stereo float */
	int plen = 0;
	char *payload = janus_rtp_payload(packet->buffer, packet->length, &plen);
	if(payload == NULL || plen <= 0) {
		janus_mutex_unlock(&session->mutex);
		return;
	}
	int samples = opus_decode_float(session->dec, (const unsigned char *)payload, plen,
		session->decbuf, SLV_DECODE_MAX, 0);
	if(samples < 0) {
		g_atomic_int_set(&session->last_decode_ok, 0);
		janus_mutex_unlock(&session->mutex);
		JANUS_LOG(LOG_WARN, "[%s-%p] Opus decode error: %s\n",
			JANUS_SLVOICE_PACKAGE, handle, opus_strerror(samples));
		return;
	}
	g_atomic_int_set(&session->last_decode_ok, 1);
	if(samples > SLV_DECODE_MAX)
		samples = SLV_DECODE_MAX;

	/* Delay line: write freshly-decoded samples at the write cursor; read the
	 * frame that entered SLV_DELAY_SAMPLES ago (zeros for the first 500ms). */
	int wp = session->ring_wpos;
	int rp = (wp + SLV_RING_SAMPLES - SLV_DELAY_SAMPLES) % SLV_RING_SAMPLES;
	for(int i = 0; i < samples; i++) {
		session->framebuf[2*i]     = session->ring[2*rp];
		session->framebuf[2*i + 1] = session->ring[2*rp + 1];
		session->ring[2*wp]     = session->decbuf[2*i];
		session->ring[2*wp + 1] = session->decbuf[2*i + 1];
		wp = (wp + 1) % SLV_RING_SAMPLES;
		rp = (rp + 1) % SLV_RING_SAMPLES;
	}
	session->ring_wpos = wp;

	/* Re-encode the delayed frame and build an RTP packet around it */
	int enc_len = opus_encode_float(session->enc, session->framebuf, samples,
		session->outbuf + 12, SLV_RTP_OUT_MAX - 12);
	if(enc_len < 0) {
		janus_mutex_unlock(&session->mutex);
		JANUS_LOG(LOG_WARN, "[%s-%p] Opus encode error: %s\n",
			JANUS_SLVOICE_PACKAGE, handle, opus_strerror(enc_len));
		return;
	}
	janus_rtp_header *rtp = (janus_rtp_header *)session->outbuf;
	memset(rtp, 0, 12);
	rtp->version = 2;
	rtp->type = (session->opus_pt > 0 ? session->opus_pt : 111);
	rtp->markerbit = 0;
	rtp->seq_number = htons(session->out_seq++);
	rtp->timestamp = htonl(session->out_ts);
	rtp->ssrc = htonl(session->out_ssrc);
	session->out_ts += samples;   /* 48k clock, per-channel samples */

	janus_plugin_rtp outp = { .mindex = -1, .video = FALSE,
		.buffer = (char *)session->outbuf, .length = (uint16_t)(12 + enc_len) };
	janus_plugin_rtp_extensions_reset(&outp.extensions);
	/* Relay under the session lock so echo-disable can't free outbuf mid-send. */
	gateway->relay_rtp(handle, &outp);
	SLV_MEDIA_LOG("[%s-%p] echo: in %d bytes -> %d samples -> out %d bytes (pt=%d)\n",
		JANUS_SLVOICE_PACKAGE, handle, plen, samples, enc_len, rtp->type);
	janus_mutex_unlock(&session->mutex);
}

void janus_slvoice_incoming_rtcp(janus_plugin_session *handle, janus_plugin_rtcp *packet) {
	/* Phase 1: RTCP is not acted on (no mixing/feedback loop yet). */
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

	/* Echo toggle (slvoice extension) */
	if(d.fields_seen & SLV_FIELD_ECHO) {
		janus_mutex_lock(&session->mutex);
		if(d.echo) {
			if(janus_slvoice_echo_start_locked(session))
				JANUS_LOG(LOG_INFO, "[%s-%p] Echo mode ENABLED (500ms delay)\n", JANUS_SLVOICE_PACKAGE, handle);
			else
				JANUS_LOG(LOG_ERR, "[%s-%p] Echo mode enable FAILED (codec init)\n", JANUS_SLVOICE_PACKAGE, handle);
		} else {
			janus_slvoice_echo_stop_locked(session);
			JANUS_LOG(LOG_INFO, "[%s-%p] Echo mode DISABLED\n", JANUS_SLVOICE_PACKAGE, handle);
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
	/* Phase 1 stores sp/sh/lp/lh/m/ug (in session->last_data) but does not act
	 * on the geometry — spatial mixing is Phase 2. */
}

void janus_slvoice_data_ready(janus_plugin_session *handle) {
	janus_slvoice_session *session = (janus_slvoice_session *)handle->plugin_handle;
	if(session == NULL)
		return;
	if(g_atomic_int_compare_and_exchange(&session->dc_open, 0, 1))
		JANUS_LOG(LOG_INFO, "[%s-%p] Data channel open\n", JANUS_SLVOICE_PACKAGE, handle);
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
	g_atomic_int_set(&session->last_decode_ok, -1);
	janus_mutex_lock(&session->mutex);
	janus_slvoice_echo_stop_locked(session);
	janus_mutex_unlock(&session->mutex);
}
