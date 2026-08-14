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
 * Explicitly OUT OF SCOPE for 1A: audio mixing, echo, Opus decode/encode, RTP
 * forwarding. incoming_rtp only ingests and counts packets.
 *
 * Written against the Janus 1.4.1 plugin API (JANUS_PLUGIN_API_VERSION 106);
 * the vendored headers and in-tree plugins are the authority.
 */

#include <inttypes.h>
#include <string.h>

#include <jansson.h>

#include <janus/plugins/plugin.h>
#include <janus/debug.h>
#include <janus/config.h>
#include <janus/mutex.h>
#include <janus/refcount.h>
#include <janus/utils.h>
#include <janus/sdp-utils.h>

#include "sldata.h"

/* Plugin information */
#define JANUS_SLVOICE_VERSION         4
#define JANUS_SLVOICE_VERSION_STRING  "0.4.0"
#define JANUS_SLVOICE_DESCRIPTION     "Spatial voice mixer for OpenSimulator, speaking the Second Life WebRTC voice protocol (Phase 1A: holds a WebRTC voice session incl. the SLData data channel; no audio yet)."
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
#define JANUS_SLVOICE_ERROR_UNKNOWN         499

/* Mixer->client SLData power/VAD batch cadence (spec §9: ~100ms). */
#define SLV_POWER_TICK_MS   100

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
	volatile gint destroyed;
	janus_refcount ref;
} janus_slvoice_room;

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

	/* Diagnostics (guarded by mutex except where atomic) */
	guint64 rtp_in_count;        /* RTP packets ingested (counted only; not processed) */
	guint64 data_msgs_received;  /* SLData messages received on the data channel */
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
	room->sampling_rate = rate ? rate : 48000;
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
	if(!g_atomic_int_get(&s->dc_open))
		return;
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
	json_object_set_new(sub, join ? "j" : "l", json_true());
	json_object_set_new(entry, who, sub);
	janus_mutex_lock(&room->mutex);
	GHashTableIter iter;
	gpointer value;
	g_hash_table_iter_init(&iter, room->participants);
	while(g_hash_table_iter_next(&iter, NULL, &value)) {
		janus_slvoice_session *p = value;
		if(p->display != NULL && !strcmp(p->display, who))
			continue;   /* skip the subject itself */
		janus_slvoice_relay_json(p, entry);
	}
	janus_mutex_unlock(&room->mutex);
	json_decref(entry);
}

/* ---- Session helpers ----------------------------------------------------- */

static void janus_slvoice_session_free(const janus_refcount *ref) {
	janus_slvoice_session *session = janus_refcount_containerof(ref, janus_slvoice_session, ref);
	JANUS_LOG(LOG_VERB, "[%s] Freeing session %p\n", JANUS_SLVOICE_PACKAGE, session);
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
	/* Drop room membership before dropping the session */
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
	json_object_set_new(info, "ice_state", json_string(up ? "connected" : "disconnected"));
	json_object_set_new(info, "dtls_state", json_string(up ? "connected" : "disconnected"));
	json_object_set_new(info, "datachannel_negotiated", session->has_datachannel ? json_true() : json_false());
	json_object_set_new(info, "datachannel_answered", session->dc_answered ? json_true() : json_false());
	json_object_set_new(info, "datachannel_open", g_atomic_int_get(&session->dc_open) ? json_true() : json_false());
	if(session->room != NULL)
		json_object_set_new(info, "room", json_integer((json_int_t)session->room->room_id));
	json_object_set_new(info, "id", json_integer((json_int_t)session->user_id));
	if(session->display)
		json_object_set_new(info, "display", json_string(session->display));
	json_object_set_new(info, "opus_pt", json_integer(session->opus_pt));
	json_object_set_new(info, "rtp_in_count", json_integer((json_int_t)session->rtp_in_count));
	json_object_set_new(info, "data_msgs_received", json_integer((json_int_t)session->data_msgs_received));
	char fbuf[96];
	slv_sldata_fields_str(session->last_data_fields, fbuf, sizeof(fbuf));
	json_object_set_new(info, "last_data_fields_seen", json_string(fbuf));
	gint64 uptime = (janus_get_monotonic_time() - session->created_ts) / G_USEC_PER_SEC;
	json_object_set_new(info, "session_uptime", json_integer((json_int_t)uptime));
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

	/* Build the answer: accept Opus audio sendrecv AND the datachannel; reject
	 * everything else (generate_answer defaults every m-line to rejected). */
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
			/* THE critical line: accept + answer the SCTP DataChannel so the
			 * core sets up SCTP (sdp.c:1611) instead of skipping it (sdp.c:1625).
			 * Minimal correct option list; proto (UDP/DTLS/SCTP) and port are
			 * filled by generate_answer_mline from the offer. */
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
			/* One batch for the whole room (all zeros for every participant) */
			json_t *batch = json_object();
			GHashTableIter piter;
			gpointer pvalue;
			g_hash_table_iter_init(&piter, room->participants);
			while(g_hash_table_iter_next(&piter, NULL, &pvalue)) {
				janus_slvoice_session *p = pvalue;
				if(p->display == NULL)
					continue;
				json_t *pv = json_object();
				json_object_set_new(pv, "p", json_integer(0));   /* power = RMS*128 (silence) */
				json_object_set_new(pv, "V", json_false());      /* VAD (not talking) */
				json_object_set_new(batch, p->display, pv);
			}
			char *text = json_dumps(batch, JSON_COMPACT);
			if(text != NULL) {
				size_t len = strlen(text);
				if(len < 65536) {
					g_hash_table_iter_init(&piter, room->participants);
					while(g_hash_table_iter_next(&piter, NULL, &pvalue)) {
						janus_slvoice_session *p = pvalue;
						if(!g_atomic_int_get(&p->dc_open))
							continue;
						janus_plugin_data d = { .label = NULL, .protocol = NULL, .binary = FALSE,
							.buffer = text, .length = (uint16_t)len };
						gateway->relay_data(p->handle, &d);
					}
				}
				free(text);
			}
			json_decref(batch);
			janus_mutex_unlock(&room->mutex);
		}
		janus_mutex_unlock(&rooms_mutex);
	}
	JANUS_LOG(LOG_VERB, "[%s] Leaving sender thread\n", JANUS_SLVOICE_PACKAGE);
	return NULL;
}

/* ---- Media callbacks ----------------------------------------------------- */

void janus_slvoice_setup_media(janus_plugin_session *handle) {
	janus_slvoice_session *session = (janus_slvoice_session *)handle->plugin_handle;
	if(session == NULL)
		return;
	g_atomic_int_set(&session->webrtc_up, 1);
	JANUS_LOG(LOG_INFO, "[%s-%p] WebRTC media is now available (ICE connected, DTLS complete, PeerConnection up)\n",
		JANUS_SLVOICE_PACKAGE, handle);
}

void janus_slvoice_incoming_rtp(janus_plugin_session *handle, janus_plugin_rtp *packet) {
	if(g_atomic_int_get(&stopping) || !g_atomic_int_get(&initialized))
		return;
	janus_slvoice_session *session = (janus_slvoice_session *)handle->plugin_handle;
	if(session == NULL || g_atomic_int_get(&session->destroyed))
		return;
	if(packet == NULL || packet->buffer == NULL || packet->video)
		return;   /* audio only; Phase 1A ingests and counts, never processes */
	janus_mutex_lock(&session->mutex);
	session->rtp_in_count++;
	janus_mutex_unlock(&session->mutex);
	SLV_MEDIA_LOG("[%s-%p] incoming_rtp %d bytes (counted; not processed)\n",
		JANUS_SLVOICE_PACKAGE, handle, packet->length);
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
	if(g_atomic_int_compare_and_exchange(&session->dc_open, 0, 1))
		JANUS_LOG(LOG_INFO, "[%s-%p] Data channel open (writable)\n", JANUS_SLVOICE_PACKAGE, handle);
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
}
