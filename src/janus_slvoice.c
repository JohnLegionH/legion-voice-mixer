/*! \file    janus_slvoice.c
 * \author   Legion Voice Mixer project
 * \copyright GNU General Public License v3
 * \brief    Janus SLVoice plugin (SCAFFOLD)
 *
 * \details  This is the Phase-0 scaffold for \c janus.plugin.slvoice, a
 * spatial voice mixer for OpenSimulator grids speaking the Second Life
 * WebRTC voice protocol. It implements the full \c janus_plugin vtable so
 * that the Janus core will load and register it, but it contains NO audio
 * logic and NO protocol logic beyond parsing and logging the request verb
 * of incoming messages. The interesting work (the per-region 20ms mixer,
 * see src/mixer/mixer.h) is Phase 1.
 *
 * The message protocol accepted by handle_message() begins as a SUPERSET of
 * janus.plugin.audiobridge's join/leave/configure request shapes, using the
 * same field names the OpenSim C# side sends today. See docs/protocol-compat.md
 * (and docs/current-architecture.md section 3) for the constraint and its
 * planned expiry. This scaffold only recognises and logs those verbs; it does
 * not act on them.
 *
 * This file is written against the Janus 1.4.1 plugin API
 * (vendor/janus-gateway, JANUS_PLUGIN_API_VERSION 106); plugins/plugin.h is
 * the authority, not remembered signatures.
 */

#include <jansson.h>

#include <janus/plugins/plugin.h>
#include <janus/debug.h>
#include <janus/config.h>
#include <janus/mutex.h>
#include <janus/refcount.h>
#include <janus/utils.h>

/* Plugin information */
#define JANUS_SLVOICE_VERSION         1
#define JANUS_SLVOICE_VERSION_STRING  "0.0.1"
#define JANUS_SLVOICE_DESCRIPTION     "Spatial voice mixer for OpenSimulator, speaking the Second Life WebRTC voice protocol (scaffold; no audio/protocol logic yet)."
#define JANUS_SLVOICE_NAME            "Legion SLVoice mixer"
#define JANUS_SLVOICE_AUTHOR          "Legion Voice Mixer project"
#define JANUS_SLVOICE_PACKAGE         "janus.plugin.slvoice"

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

/* Per-peer session. Deliberately minimal for the scaffold: enough to be
 * refcount-safe and let the core create/query/destroy handles cleanly. The
 * mixer-facing state (participant, region membership, jitter buffer, spatial
 * position, gain) arrives in Phase 1; see src/mixer/mixer.h. */
typedef struct janus_slvoice_session {
	janus_plugin_session *handle;
	volatile gint hangingup;
	volatile gint destroyed;
	janus_refcount ref;
} janus_slvoice_session;
static GHashTable *sessions = NULL;
static janus_mutex sessions_mutex = JANUS_MUTEX_INITIALIZER;

static void janus_slvoice_session_free(const janus_refcount *ref) {
	janus_slvoice_session *session = janus_refcount_containerof(ref, janus_slvoice_session, ref);
	JANUS_LOG(LOG_VERB, "[%s] Freeing session %p\n", JANUS_SLVOICE_PACKAGE, session);
	g_free(session);
}

static void janus_slvoice_session_destroy(janus_slvoice_session *session) {
	if(session && g_atomic_int_compare_and_exchange(&session->destroyed, 0, 1))
		janus_refcount_decrease(&session->ref);
}

/* Recognised request verbs. Phase 0: this is the audiobridge superset the
 * OpenSim C# side sends today (create/destroy/join/leave/configure plus the
 * list helpers). We only LOG which of these a message carries. Do not rename
 * any of these; extensions go into new fields (see docs/protocol-compat.md). */
static gboolean janus_slvoice_known_request(const char *request) {
	if(request == NULL)
		return FALSE;
	static const char *known[] = {
		"create", "destroy", "join", "leave", "configure",
		"list", "listparticipants", NULL
	};
	for(int i = 0; known[i] != NULL; i++) {
		if(!strcasecmp(request, known[i]))
			return TRUE;
	}
	return FALSE;
}

/* Plugin implementation */
int janus_slvoice_init(janus_callbacks *callback, const char *config_path) {
	if(g_atomic_int_get(&stopping)) {
		/* Still stopping from a previous session */
		return -1;
	}
	if(callback == NULL || config_path == NULL) {
		/* Invalid arguments */
		return -1;
	}

	/* Read (optional) configuration file; nothing acted on yet in Phase 0 */
	char filename[255];
	g_snprintf(filename, sizeof(filename), "%s/%s.jcfg", config_path, JANUS_SLVOICE_PACKAGE);
	JANUS_LOG(LOG_VERB, "[%s] Configuration file: %s\n", JANUS_SLVOICE_PACKAGE, filename);
	janus_config *config = janus_config_parse(filename);
	if(config != NULL) {
		janus_config_print(config);
		janus_config_destroy(config);
	} else {
		JANUS_LOG(LOG_WARN, "[%s] No configuration file (%s) found, using defaults\n",
			JANUS_SLVOICE_PACKAGE, filename);
	}

	sessions = g_hash_table_new(NULL, NULL);

	gateway = callback;
	g_atomic_int_set(&initialized, 1);

	JANUS_LOG(LOG_INFO, "%s initialized! (API v%d, scaffold %s)\n",
		JANUS_SLVOICE_NAME, JANUS_PLUGIN_API_VERSION, JANUS_SLVOICE_VERSION_STRING);
	return 0;
}

void janus_slvoice_destroy(void) {
	if(!g_atomic_int_get(&initialized))
		return;
	g_atomic_int_set(&stopping, 1);

	janus_mutex_lock(&sessions_mutex);
	if(sessions != NULL) {
		g_hash_table_destroy(sessions);
		sessions = NULL;
	}
	janus_mutex_unlock(&sessions_mutex);

	gateway = NULL;
	g_atomic_int_set(&initialized, 0);
	g_atomic_int_set(&stopping, 0);
	JANUS_LOG(LOG_INFO, "%s destroyed!\n", JANUS_SLVOICE_NAME);
}

int janus_slvoice_get_api_compatibility(void) {
	/* Important! This is what your plugin MUST always return: don't lie here or bad things will happen */
	return JANUS_PLUGIN_API_VERSION;
}

int janus_slvoice_get_version(void) {
	return JANUS_SLVOICE_VERSION;
}

const char *janus_slvoice_get_version_string(void) {
	return JANUS_SLVOICE_VERSION_STRING;
}

const char *janus_slvoice_get_description(void) {
	return JANUS_SLVOICE_DESCRIPTION;
}

const char *janus_slvoice_get_name(void) {
	return JANUS_SLVOICE_NAME;
}

const char *janus_slvoice_get_author(void) {
	return JANUS_SLVOICE_AUTHOR;
}

const char *janus_slvoice_get_package(void) {
	return JANUS_SLVOICE_PACKAGE;
}

void janus_slvoice_create_session(janus_plugin_session *handle, int *error) {
	if(g_atomic_int_get(&stopping) || !g_atomic_int_get(&initialized)) {
		*error = -1;
		return;
	}
	janus_slvoice_session *session = g_malloc0(sizeof(janus_slvoice_session));
	session->handle = handle;
	g_atomic_int_set(&session->hangingup, 0);
	g_atomic_int_set(&session->destroyed, 0);
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
	janus_mutex_lock(&sessions_mutex);
	janus_slvoice_session *session = (janus_slvoice_session *)handle->plugin_handle;
	if(session == NULL) {
		janus_mutex_unlock(&sessions_mutex);
		JANUS_LOG(LOG_ERR, "[%s] No session associated with this handle...\n", JANUS_SLVOICE_PACKAGE);
		*error = -2;
		return;
	}
	JANUS_LOG(LOG_INFO, "[%s-%p] Destroying session\n", JANUS_SLVOICE_PACKAGE, handle);
	g_hash_table_remove(sessions, handle);
	janus_slvoice_session_destroy(session);
	janus_mutex_unlock(&sessions_mutex);
}

json_t *janus_slvoice_query_session(janus_plugin_session *handle) {
	if(g_atomic_int_get(&stopping) || !g_atomic_int_get(&initialized))
		return NULL;
	janus_mutex_lock(&sessions_mutex);
	janus_slvoice_session *session = (janus_slvoice_session *)handle->plugin_handle;
	/* Janus requires an allocated object here, never a constant */
	json_t *info = json_object();
	json_object_set_new(info, "plugin", json_string(JANUS_SLVOICE_PACKAGE));
	json_object_set_new(info, "state", json_string(session ? "attached" : "detached"));
	json_object_set_new(info, "hangingup", json_integer(session ? g_atomic_int_get(&session->hangingup) : 0));
	json_object_set_new(info, "destroyed", json_integer(session ? g_atomic_int_get(&session->destroyed) : 1));
	janus_mutex_unlock(&sessions_mutex);
	return info;
}

struct janus_plugin_result *janus_slvoice_handle_message(janus_plugin_session *handle,
		char *transaction, json_t *message, json_t *jsep) {
	/* Phase 0: parse and log the request verb only. No protocol handling.
	 * We own message/jsep/transaction and must release them here (synchronous). */
	const char *request_text = NULL;
	if(message != NULL && json_is_object(message)) {
		json_t *request = json_object_get(message, "request");
		if(json_is_string(request))
			request_text = json_string_value(request);
	}
	gboolean known = janus_slvoice_known_request(request_text);
	JANUS_LOG(LOG_INFO, "[%s-%p] handle_message: request=\"%s\" (%s), jsep=%s\n",
		JANUS_SLVOICE_PACKAGE, handle,
		request_text ? request_text : "(none)",
		known ? "recognised audiobridge-superset verb" : "unrecognised/passthrough",
		jsep ? "present" : "absent");

	/* Sane default: acknowledge synchronously with an empty result payload.
	 * Nothing is negotiated or mixed at this phase. */
	json_t *response = json_object();
	json_object_set_new(response, "slvoice", json_string("ack"));
	if(request_text != NULL)
		json_object_set_new(response, "request", json_string(request_text));

	/* Release the objects the core handed us (see plugin.h handle_message contract) */
	if(message != NULL)
		json_decref(message);
	if(jsep != NULL)
		json_decref(jsep);
	g_free(transaction);

	return janus_plugin_result_new(JANUS_PLUGIN_OK, NULL, response);
}

json_t *janus_slvoice_handle_admin_message(json_t *message) {
	/* Phase 0: log and return a trivial, allocated response */
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

void janus_slvoice_setup_media(janus_plugin_session *handle) {
	JANUS_LOG(LOG_INFO, "[%s-%p] WebRTC media is now available (no-op)\n", JANUS_SLVOICE_PACKAGE, handle);
}

void janus_slvoice_incoming_rtp(janus_plugin_session *handle, janus_plugin_rtp *packet) {
	if(g_atomic_int_get(&stopping) || !g_atomic_int_get(&initialized))
		return;
	JANUS_LOG(LOG_HUGE, "[%s-%p] incoming_rtp: %s, %d bytes (dropped; no mixer yet)\n",
		JANUS_SLVOICE_PACKAGE, handle, packet && packet->video ? "video" : "audio",
		packet ? packet->length : 0);
}

void janus_slvoice_incoming_rtcp(janus_plugin_session *handle, janus_plugin_rtcp *packet) {
	if(g_atomic_int_get(&stopping) || !g_atomic_int_get(&initialized))
		return;
	JANUS_LOG(LOG_HUGE, "[%s-%p] incoming_rtcp: %d bytes (ignored)\n",
		JANUS_SLVOICE_PACKAGE, handle, packet ? packet->length : 0);
}

void janus_slvoice_incoming_data(janus_plugin_session *handle, janus_plugin_data *packet) {
	if(g_atomic_int_get(&stopping) || !g_atomic_int_get(&initialized))
		return;
	/* The SL WebRTC voice protocol carries position updates on the data
	 * channel; parsing/routing them is Phase 1. For now, just log. */
	JANUS_LOG(LOG_VERB, "[%s-%p] incoming_data: label=\"%s\", %s, %d bytes (logged only)\n",
		JANUS_SLVOICE_PACKAGE, handle,
		packet && packet->label ? packet->label : "",
		packet && packet->binary ? "binary" : "text",
		packet ? packet->length : 0);
}

void janus_slvoice_data_ready(janus_plugin_session *handle) {
	JANUS_LOG(LOG_VERB, "[%s-%p] data_ready (no-op)\n", JANUS_SLVOICE_PACKAGE, handle);
}

void janus_slvoice_slow_link(janus_plugin_session *handle, int mindex, gboolean video, gboolean uplink) {
	JANUS_LOG(LOG_VERB, "[%s-%p] slow_link: mindex=%d, %s, %s (informational)\n",
		JANUS_SLVOICE_PACKAGE, handle, mindex, video ? "video" : "audio",
		uplink ? "uplink" : "downlink");
}

void janus_slvoice_hangup_media(janus_plugin_session *handle) {
	JANUS_LOG(LOG_INFO, "[%s-%p] No WebRTC media anymore (no-op)\n", JANUS_SLVOICE_PACKAGE, handle);
	if(g_atomic_int_get(&stopping) || !g_atomic_int_get(&initialized))
		return;
	janus_mutex_lock(&sessions_mutex);
	janus_slvoice_session *session = (janus_slvoice_session *)handle->plugin_handle;
	if(session != NULL)
		g_atomic_int_set(&session->hangingup, 0);
	janus_mutex_unlock(&sessions_mutex);
}
