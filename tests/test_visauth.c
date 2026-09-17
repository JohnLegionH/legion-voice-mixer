/*! \file    tests/test_visauth.c
 * \author   Legion Voice Mixer project
 * \copyright GNU General Public License v3
 * \brief    Unit tests for Phase 0 slice 0.3, the mixer's visibility authority
 *           (docs/voice/nonspatial-phase0-design.md).
 *
 * \details  Reaches the plugin's statics by #including janus_slvoice.c, like test_room_lifecycle, and is linked the
 * same way. Sources carry a real Opus tone through the jitter buffer (primed as tests/bench_tick.c does), so "audible"
 * and "silent" are the listener's mixed output level, last_mix_rms: the oracle the integration harness uses. The gateway
 * is a fake that records every data-channel message, so presence, backlog and dots are checked as sent. The clock is
 * fake (janus_get_monotonic_time returns fake_now_us), so the staleness window is tested to the millisecond.
 *
 * Covers: keying per room by avatar (two sessions of one display, one display in two rooms, a reconnect); each row of
 * the decision table and the pair rule, in the mix, presence, backlog, roster and dots; the staleness window and its
 * clamp; the epoch adoption rule, policy_generation ordering and delta base checks; heartbeat confirm, stale, unarmed,
 * omission, graceful stop and takeover; a room without vis_authority with fail-closed on; fail-closed off (shadow mode)
 * against an undeclared room, byte for byte; would_silence_* counting; pre-join arming; and the reply keys, including
 * replies to a sim that sends no stamp.
 *
 * NOT covered here (the integration harness covers them): the create request's vis_authority key and the join arm's
 * negotiate path (both need the handler thread and a JSEP offer), and a real restart's new mixer_instance on the wire.
 */

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <math.h>

#include "../src/janus_slvoice.c"

/* ---- Janus core symbols the plugin leaves undefined (see tests/bench_tick.c) ---- */
int      janus_log_level      = 0;      /* JANUS_LOG guard fails => silent */
gboolean janus_log_timestamps = FALSE;
gboolean janus_log_colors     = FALSE;
char    *janus_log_global_prefix = NULL;
int      lock_debug           = 0;
int      refcount_debug       = 0;
void janus_vprintf(const char *format, ...) { (void)format; }
char *janus_rtp_payload(char *buf, int len, int *plen) { (void)buf; (void)len; if(plen) *plen = 0; return NULL; }
/* The fake clock: every test runs on it, so the window arithmetic is exact. */
static gint64 fake_now_us = 0;
gint64 janus_get_monotonic_time(void) { return fake_now_us != 0 ? fake_now_us : g_get_monotonic_time(); }
static guint64 s_rng = 0x9e3779b97f4a7c15ULL;
guint64 janus_random_uint64(void) { s_rng = s_rng * 6364136223846793005ULL + 1442695040888963407ULL; return s_rng; }
janus_plugin_result *janus_plugin_result_new(janus_plugin_result_type type, const char *text, json_t *content) {
	(void)type; (void)text; (void)content; return NULL;
}
void janus_plugin_rtp_extensions_reset(janus_plugin_rtp_extensions *extensions) { (void)extensions; }

static int g_failures = 0;
static int g_checks = 0;

#define CHECK(cond, msg) do { \
	g_checks++; \
	if(!(cond)) { \
		g_failures++; \
		fprintf(stderr, "  FAIL: %s (%s:%d)\n", (msg), __FILE__, __LINE__); \
	} \
} while(0)

#define E1 "0000018f00000001"
#define E2 "0000018f00000002"
#define E_LOWER "0000018e00000009"
#define E1V G_GUINT64_CONSTANT(0x0000018f00000001)
#define E2V G_GUINT64_CONSTANT(0x0000018f00000002)
#define E_LOWERV G_GUINT64_CONSTANT(0x0000018e00000009)
#define TICK_US ((gint64)SLV_TICK_MS * 1000)

/* ---- the fake gateway: every data-channel message, per handle ---- */
static GHashTable *sent;   /* janus_plugin_session* -> GString* ("\n"-separated messages) */
static void fake_relay_rtp(janus_plugin_session *h, janus_plugin_rtp *p) { (void)h; (void)p; }
static void fake_relay_data(janus_plugin_session *h, janus_plugin_data *p) {
	GString *g = g_hash_table_lookup(sent, h);
	if(g == NULL) {
		g = g_string_new(NULL);
		g_hash_table_insert(sent, h, g);
	}
	g_string_append_len(g, p->buffer, (gssize)p->length);
	g_string_append_c(g, '\n');
}
static int fake_push_event(janus_plugin_session *h, janus_plugin *pl, const char *tx, json_t *m, json_t *j) {
	(void)h; (void)pl; (void)tx; (void)m; (void)j; return 0;
}
static gboolean fake_events_is_enabled(void) { return FALSE; }
static janus_callbacks fake_gateway = {
	.relay_rtp = fake_relay_rtp,
	.relay_data = fake_relay_data,
	.push_event = fake_push_event,
	.events_is_enabled = fake_events_is_enabled,
};
static void sent_free(gpointer g) { g_string_free((GString *)g, TRUE); }
static void clear_sent(void) { g_hash_table_remove_all(sent); }
static const char *sent_text(janus_slvoice_session *s) {
	GString *g = g_hash_table_lookup(sent, s->handle);
	return g != NULL ? g->str : "";
}
/* Did s receive {who:{what...}}? what: "j" join, "l" leave, "p" a power entry. */
static gboolean got(janus_slvoice_session *s, const char *who, const char *what) {
	char needle[96];
	g_snprintf(needle, sizeof(needle), "\"%s\":{\"%s\"", who, what);
	return strstr(sent_text(s), needle) != NULL;
}

/* ---- a real Opus tone (tests/bench_tick.c) ---- */
#define TONE_FRAMES 8
static unsigned char tone_frame[TONE_FRAMES][SLV_JB_PAYLOAD_MAX];
static int tone_len[TONE_FRAMES];
static void gen_tone(void) {
	int err = 0;
	OpusEncoder *enc = opus_encoder_create(SLV_RATE, SLV_CHANNELS, OPUS_APPLICATION_VOIP, &err);
	if(err != OPUS_OK || enc == NULL) { fprintf(stderr, "test_visauth: opus_encoder_create failed\n"); exit(2); }
	opus_encoder_ctl(enc, OPUS_SET_BITRATE(SLV_OPUS_BITRATE));
	static float pcm[SLV_FRAME_SAMPLES * SLV_CHANNELS];
	for(int f = 0; f < TONE_FRAMES; f++) {
		for(int n = 0; n < SLV_FRAME_SAMPLES; n++) {
			double t = (double)(f * SLV_FRAME_SAMPLES + n) / (double)SLV_RATE;
			float v = 0.5f * (float)sin(2.0 * M_PI * 440.0 * t);
			pcm[2 * n] = v;
			pcm[2 * n + 1] = v;
		}
		tone_len[f] = opus_encode_float(enc, pcm, SLV_FRAME_SAMPLES, tone_frame[f], SLV_JB_PAYLOAD_MAX);
		if(tone_len[f] < 0) { fprintf(stderr, "test_visauth: opus_encode_float failed\n"); exit(2); }
	}
	opus_encoder_destroy(enc);
}

typedef struct tone_src {
	janus_slvoice_session *s;
	uint16_t seq;
} tone_src;

static void tone_feed(tone_src *t, int frames) {
	janus_mutex_lock(&t->s->mutex);
	for(int i = 0; i < frames; i++) {
		janus_slvoice_jb_insert(t->s, t->seq, tone_frame[t->seq % TONE_FRAMES], tone_len[t->seq % TONE_FRAMES]);
		t->seq++;
	}
	t->s->last_rtp_us = janus_get_monotonic_time();
	janus_mutex_unlock(&t->s->mutex);
}

static void tone_prime(tone_src *t) {
	tone_feed(t, SLV_JB_LAG + 2);
}

/* One frame from each source, one tick, 20 ms of fake time; `ticks` times. */
static void run_ticks(janus_slvoice_room *room, tone_src *srcs, int nsrc, int ticks) {
	for(int k = 0; k < ticks; k++) {
		for(int i = 0; i < nsrc; i++)
			tone_feed(&srcs[i], 1);
		janus_slvoice_room_tick(room);
		fake_now_us += TICK_US;
	}
}

/* ---- sessions and rooms ---- */
static void noop_free(const janus_refcount *r) { (void)r; }

static janus_slvoice_session *new_session(const char *display) {
	janus_slvoice_session *s = g_malloc0(sizeof(janus_slvoice_session));
	s->opus_pt = -1;
	s->handle = g_malloc0(sizeof(janus_plugin_session));
	s->handle->plugin_handle = s;
	s->display = display != NULL ? g_strdup(display) : NULL;
	s->excluded = g_hash_table_new_full(g_str_hash, g_str_equal, g_free, NULL);
	s->mod_muted = g_hash_table_new_full(g_str_hash, g_str_equal, g_free, NULL);
	janus_mutex_init(&s->mutex);
	janus_refcount_init(&s->ref, noop_free);
	janus_mutex_lock(&s->mutex);
	if(!janus_slvoice_media_alloc_locked(s)) { fprintf(stderr, "test_visauth: media_alloc failed\n"); exit(2); }
	janus_mutex_unlock(&s->mutex);
	g_atomic_int_set(&s->webrtc_up, 1);
	g_atomic_int_set(&s->dc_open, 1);
	return s;
}

static void free_session(janus_slvoice_session *s) {
	janus_slvoice_leave_room(s);
	janus_mutex_lock(&s->mutex);
	janus_slvoice_media_free_locked(s);
	janus_mutex_unlock(&s->mutex);
	g_hash_table_destroy(s->excluded);
	g_hash_table_destroy(s->mod_muted);
	g_free(s->display);
	g_free(s->handle);
	g_free(s);
}

/* A flat (non-spatial) room with its tick thread stopped: the test drives the tick. */
static janus_slvoice_room *new_room(guint64 id, gboolean declared) {
	janus_slvoice_room *room = janus_slvoice_room_create(id, NULL, FALSE, 48000, FALSE, FALSE, declared);
	if(room == NULL) {
		fprintf(stderr, "test_visauth: room_create(%" PRIu64 ") failed\n", id);
		exit(2);
	}
	janus_slvoice_room_stop(room);
	janus_mutex_lock(&rooms_mutex);
	guint64 *key = g_malloc(sizeof(guint64));
	*key = id;
	g_hash_table_insert(rooms, key, room);
	janus_mutex_unlock(&rooms_mutex);
	return room;
}

/* The join arm after negotiate: membership, the commit under room->mutex, then the presence push. */
static void join(janus_slvoice_room *room, janus_slvoice_session *s, json_t **roster) {
	static guint64 next_uid = 1000;
	janus_refcount_increase(&room->ref);
	janus_mutex_lock(&s->mutex);
	s->user_id = ++next_uid;
	s->room = room;
	janus_mutex_unlock(&s->mutex);
	janus_mutex_lock(&room->mutex);
	guint64 *key = g_malloc(sizeof(guint64));
	*key = s->user_id;
	g_hash_table_insert(room->participants, key, s);
	room->empty_since = 0;
	json_t *list = janus_slvoice_join_commit_locked(room, s, s->display, room->room_id, s->user_id);
	janus_mutex_unlock(&room->mutex);
	if(roster != NULL)
		*roster = list;
	else
		json_decref(list);
	janus_slvoice_push_presence(room, s->display, TRUE);
}

static gboolean roster_has(json_t *list, const char *display) {
	size_t i;
	json_t *row;
	json_array_foreach(list, i, row)
		if(!g_strcmp0(json_string_value(json_object_get(row, "display")), display))
			return TRUE;
	return FALSE;
}

static int row_of(janus_slvoice_room *room, const char *display) {
	janus_mutex_lock(&room->mutex);
	int r = janus_slvoice_vis_row_locked(room, display, janus_get_monotonic_time());
	janus_mutex_unlock(&room->mutex);
	return r;
}

static slv_vis_record *record_of(janus_slvoice_room *room, const char *display) {
	janus_mutex_lock(&room->mutex);
	slv_vis_record *r = g_hash_table_lookup(room->vis_records, display);
	janus_mutex_unlock(&room->mutex);
	return r;
}

/* The sender's fail-closed pass for one room: transitions now; returns the hidden-display count. */
static guint sender_pass(janus_slvoice_room *room) {
	janus_mutex_lock(&room->mutex);
	GHashTable *hidden = janus_slvoice_vis_transitions_locked(room, janus_get_monotonic_time());
	guint n = g_hash_table_size(hidden);
	g_hash_table_destroy(hidden);
	janus_mutex_unlock(&room->mutex);
	return n;
}

/* ---- the admin surface ---- */
static json_t *admin(const char *fmt, ...) {
	va_list ap;
	va_start(ap, fmt);
	char *text = g_strdup_vprintf(fmt, ap);
	va_end(ap);
	json_error_t err;
	json_t *msg = json_loads(text, 0, &err);
	if(msg == NULL) {
		fprintf(stderr, "test_visauth: bad test JSON: %s (%s)\n", text, err.text);
		exit(2);
	}
	g_free(text);
	json_t *reply = janus_slvoice_handle_admin_message(msg);
	json_decref(msg);
	return reply;
}

/* An arming replace (§2): each named listener with both columns empty. b may be NULL. */
static json_t *arm(guint64 room, const char *epoch, int gen, const char *a, const char *b) {
	if(b == NULL)
		return admin("{\"request\":\"peer_ctl_batch\",\"op\":\"replace\",\"room\":%" G_GUINT64_FORMAT ",\"excl\":{\"%s\":[]},"
			"\"mute\":{\"%s\":[]},\"room_epoch\":\"%s\",\"policy_generation\":%d}", room, a, a, epoch, gen);
	return admin("{\"request\":\"peer_ctl_batch\",\"op\":\"replace\",\"room\":%" G_GUINT64_FORMAT ",\"excl\":{\"%s\":[],\"%s\":[]},"
		"\"mute\":{\"%s\":[],\"%s\":[]},\"room_epoch\":\"%s\",\"policy_generation\":%d}", room, a, b, a, b, epoch, gen);
}

static json_t *heartbeat(const char *epoch, guint64 room, const char *listeners, gboolean stopping) {
	return admin("{\"request\":\"peer_ctl_heartbeat\",\"room_epoch\":\"%s\",\"interval_ms\":1000,%s\"rooms\":{\"%" G_GUINT64_FORMAT
		"\":{\"policy_generation\":1,\"listeners\":%s}}}", epoch, stopping ? "\"state\":\"stopping\"," : "", room, listeners);
}

static const char *str_of(json_t *o, const char *k) {
	const char *v = json_string_value(json_object_get(o, k));
	return v != NULL ? v : "";
}

static gboolean list_has(json_t *o, const char *k, const char *s) {
	size_t i;
	json_t *v;
	json_array_foreach(json_object_get(o, k), i, v)
		if(!g_strcmp0(json_string_value(v), s))
			return TRUE;
	return FALSE;
}

static json_t *room_reply(json_t *hb, guint64 room) {
	char key[32];
	g_snprintf(key, sizeof(key), "%" G_GUINT64_FORMAT, room);
	return json_object_get(json_object_get(hb, "rooms"), key);
}

static gboolean is_status(json_t *o, const char *status) {
	return o != NULL && !strcmp(str_of(o, "status"), status);
}

static void drop(json_t *reply) {
	json_decref(reply);
}

/* ======================================================================================================= */

/* The pure rules (visauth.h): epoch format, adoption, rows, pairs. */
static void test_pure_rules(void) {
	uint64_t e = 0;
	CHECK(slv_vis_parse_epoch("0000018f3a2b4c5d", &e) && e == G_GUINT64_CONSTANT(0x0000018f3a2b4c5d), "epoch: 16 hex digits parse");
	CHECK(!slv_vis_parse_epoch("0000000000000000", &e) && !slv_vis_parse_epoch("18f3a2b4c5d", &e)
		&& !slv_vis_parse_epoch("0000018f3a2b4c5dd", &e) && !slv_vis_parse_epoch(NULL, &e), "epoch: zero, short, long and NULL rejected");
	char buf[17];
	slv_vis_format_epoch(0x1ab, buf);
	CHECK(!strcmp(buf, "00000000000001ab"), "epoch: formatted as 16 lowercase hex digits");

	gint64 w = 8000000;
	CHECK(slv_vis_epoch_decide(0, 0, 100, w, 5) == SLV_VIS_EPOCH_ADOPT, "adoption: nothing stored -> adopt");
	CHECK(slv_vis_epoch_decide(5, 100, 200, w, 6) == SLV_VIS_EPOCH_ADOPT, "adoption: greater -> adopt");
	CHECK(slv_vis_epoch_decide(5, 100, 200, w, 5) == SLV_VIS_EPOCH_EQUAL, "adoption: equal -> normal processing");
	CHECK(slv_vis_epoch_decide(5, 100, 100 + w, w, 4) == SLV_VIS_EPOCH_STALE, "adoption: lower while fresh (exactly one window) -> stale_epoch");
	CHECK(slv_vis_epoch_decide(5, 100, 100 + w + 1, w, 4) == SLV_VIS_EPOCH_TAKEOVER, "adoption: lower once stale -> takeover");
	CHECK(slv_vis_epoch_decide(5, 0, 100, w, 4) == SLV_VIS_EPOCH_TAKEOVER, "adoption: lower after a stopping authority -> takeover");

	CHECK(slv_vis_row(0, 0, 7, 0, 0, 1000, w) == 1, "row 1: unarmed");
	CHECK(slv_vis_row(1, 6, 7, 1000, 0, 1000, w) == 2, "row 2: armed, epoch mismatch");
	CHECK(slv_vis_row(1, 7, 0, 1000, 0, 1000, w) == 2, "row 2: armed, the room holds no epoch");
	CHECK(slv_vis_row(1, 6, 7, 1000, 1, 1000 + 3 * w, w) == 2, "row 2: freshness is not evaluated on an epoch mismatch");
	CHECK(slv_vis_row(1, 7, 7, 1000, 0, 1000 + w + 1, w) == 3, "row 3: epoch match, not confirmed within the window");
	CHECK(slv_vis_row(1, 7, 7, 1000, 1, 1000, w) == 3, "row 3: epoch match, marked stale");
	CHECK(slv_vis_row(1, 7, 7, 1000, 0, 1000 + w, w) == 4, "row 4: epoch match, confirmed exactly one window ago");

	CHECK(slv_vis_would_silence_pairs(0, 0) == 0 && slv_vis_would_silence_pairs(1, 0) == 0
		&& slv_vis_would_silence_pairs(2, 1) == 2 && slv_vis_would_silence_pairs(3, 0) == 6
		&& slv_vis_would_silence_pairs(3, 3) == 0 && slv_vis_would_silence_pairs(4, 2) == 10,
		"would_silence_pairs: the ordered pairs with a side not in row 4");
}

/* §5, §6.1: the knobs, the computed minimum clamp, and the plugin's loader. */
static void test_knobs_and_clamp(void) {
	int fc = -1;
	unsigned ms = 0;
	CHECK(slv_vis_stale_min_ms(SLV_VIS_HEARTBEAT_MS) == 7250, "clamp: the computed minimum is 2 x 1000 + 5000 + 250 = 7250 ms");
	CHECK(slv_vis_fail_closed_from_env(NULL, &fc) == SLV_VIS_KNOB_OK && fc == 0, "JS_VIS_FAIL_CLOSED unset: disabled");
	CHECK(slv_vis_fail_closed_from_env("0", &fc) == SLV_VIS_KNOB_OK && fc == 0, "JS_VIS_FAIL_CLOSED=0: disabled");
	CHECK(slv_vis_fail_closed_from_env("1", &fc) == SLV_VIS_KNOB_OK && fc == 1, "JS_VIS_FAIL_CLOSED=1: enabled");
	CHECK(slv_vis_fail_closed_from_env("maybe", &fc) == SLV_VIS_KNOB_INVALID && fc == 0, "JS_VIS_FAIL_CLOSED invalid: disabled");
	CHECK(slv_vis_stale_ms_from_env(NULL, &ms) == SLV_VIS_KNOB_OK && ms == 8000, "JS_VIS_STALE_MS unset: 8000");
	CHECK(slv_vis_stale_ms_from_env("8000", &ms) == SLV_VIS_KNOB_OK && ms == 8000, "JS_VIS_STALE_MS=8000");
	CHECK(slv_vis_stale_ms_from_env("7250", &ms) == SLV_VIS_KNOB_OK && ms == 7250, "JS_VIS_STALE_MS at the minimum is kept");
	CHECK(slv_vis_stale_ms_from_env("7249", &ms) == SLV_VIS_KNOB_CLAMPED && ms == 7250, "JS_VIS_STALE_MS one below the minimum is clamped");
	CHECK(slv_vis_stale_ms_from_env("1", &ms) == SLV_VIS_KNOB_CLAMPED && ms == 7250, "JS_VIS_STALE_MS=1 is clamped");
	CHECK(slv_vis_stale_ms_from_env("600000", &ms) == SLV_VIS_KNOB_OK && ms == 600000, "JS_VIS_STALE_MS at the maximum is kept");
	CHECK(slv_vis_stale_ms_from_env("600001", &ms) == SLV_VIS_KNOB_INVALID && ms == 8000
		&& slv_vis_stale_ms_from_env("0", &ms) == SLV_VIS_KNOB_INVALID && slv_vis_stale_ms_from_env("-5", &ms) == SLV_VIS_KNOB_INVALID
		&& slv_vis_stale_ms_from_env("8s", &ms) == SLV_VIS_KNOB_INVALID && ms == 8000, "JS_VIS_STALE_MS invalid: the default");

	unsetenv("JS_VIS_FAIL_CLOSED");
	unsetenv("JS_VIS_STALE_MS");
	janus_slvoice_vis_load_knobs();
	char first[17];
	g_strlcpy(first, slv_mixer_instance, sizeof(first));
	CHECK(!slv_vis_fail_closed && slv_vis_stale_ms == 8000 && strlen(first) == 16, "loader defaults: fail-closed off, 8000 ms, a 16-digit mixer_instance");
	setenv("JS_VIS_FAIL_CLOSED", "1", 1);
	setenv("JS_VIS_STALE_MS", "5000", 1);
	janus_slvoice_vis_load_knobs();
	CHECK(slv_vis_fail_closed && slv_vis_stale_ms == 7250, "loader: JS_VIS_FAIL_CLOSED=1 enables; JS_VIS_STALE_MS=5000 is clamped to 7250");
	CHECK(strcmp(first, slv_mixer_instance) != 0, "loader: each init picks a new mixer_instance");
	unsetenv("JS_VIS_FAIL_CLOSED");
	unsetenv("JS_VIS_STALE_MS");
	janus_slvoice_vis_load_knobs();
	CHECK(!slv_vis_fail_closed && slv_vis_stale_ms == 8000, "loader: back to the defaults");
}

/* WORK 1: keyed per room by avatar. Two sessions of one display share a record; the same display in two rooms has two
 * independent records; a reconnect (a new session for the same avatar) keeps the record. */
static void test_keying(void) {
	slv_vis_fail_closed = TRUE;
	janus_slvoice_room *a = new_room(3001, TRUE);
	janus_slvoice_room *b = new_room(3002, TRUE);
	janus_slvoice_session *l1 = new_session("L"), *l2 = new_session("L"), *s = new_session("S"), *lb = new_session("L");
	join(a, l1, NULL);
	join(a, l2, NULL);
	join(a, s, NULL);
	join(b, lb, NULL);

	json_t *r = arm(3001, E1, 1, "L", "S");
	CHECK(!strcmp(str_of(r, "slvoice"), "applied") && is_status(r, "ok"), "keying: arming in room A applied");
	drop(r);
	CHECK(g_hash_table_size(a->vis_records) == 2 && g_hash_table_size(b->vis_records) == 0,
		"keying: one record per display in A (L's two sessions share one), none in B");
	CHECK(row_of(a, "L") == 4 && row_of(b, "L") == 1, "keying: the same avatar is armed in A and unarmed in B");

	r = arm(3002, E2, 1, "L", NULL);
	drop(r);
	CHECK(row_of(b, "L") == 4 && a->vis_auth_epoch == E1V && b->vis_auth_epoch == E2V && record_of(a, "L")->epoch == E1V,
		"keying: arming L in B under another epoch leaves A's record and epoch alone");
	r = heartbeat(E1, 3001, "{\"L\":99,\"S\":1}", FALSE);
	CHECK(list_has(room_reply(r, 3001), "stale_listeners", "L"), "keying: a wrong generation in A reports L stale");
	drop(r);
	CHECK(row_of(a, "L") == 3 && row_of(b, "L") == 4, "keying: L is stale in A and still fresh in B");
	drop(arm(3001, E1, 2, "L", NULL));
	CHECK(row_of(a, "L") == 4, "keying: a replace clears the stale mark");

	/* A reconnect: every session of L leaves A; the record stays, and the new session is armed from its join. */
	janus_slvoice_leave_room(l1);
	janus_slvoice_leave_room(l2);
	CHECK(row_of(a, "L") == 4 && record_of(a, "L") != NULL, "reconnect: the record survives every session of L leaving");
	janus_slvoice_session *l3 = new_session("L");
	join(a, l3, NULL);
	CHECK(l3->vis_ok_last && row_of(a, "L") == 4, "reconnect: a new session for L is in row 4 from its join");

	free_session(l1);
	free_session(l2);
	free_session(l3);
	free_session(s);
	free_session(lb);
	slv_vis_fail_closed = FALSE;
}

/* WORK 2, §4 with fail-closed on in a declared room: each row, the pair rule, the staleness window, recovery,
 * per-listener staleness in a live room, and a new epoch. Audio is the listener's mixed output level. */
static void test_decision_table(void) {
	slv_vis_fail_closed = TRUE;
	slv_vis_stale_ms = 8000;
	janus_slvoice_room *room = new_room(3101, TRUE);
	janus_slvoice_session *l = new_session("L"), *s = new_session("S");
	clear_sent();
	join(room, s, NULL);
	json_t *roster = NULL;
	join(room, l, &roster);
	tone_src src = { s, 0 };
	tone_prime(&src);

	/* Row 1: nobody armed. */
	CHECK(json_array_size(roster) == 0, "row 1: an unarmed joiner's initial roster omits the source");
	json_decref(roster);
	CHECK(!got(l, "S", "j") && !got(s, "L", "j"), "row 1: no join presence either way");
	run_ticks(room, &src, 1, 10);
	CHECK(l->last_mix_rms == 0.0, "row 1: an unarmed listener's mix is silent");
	CHECK(room->vis_ws_listeners == 2 && room->vis_ws_pairs == 2, "row 1: would_silence counts both participants and both pairs");
	janus_mutex_lock(&room->mutex);
	json_t *batch = json_pack("{s{sisb}s{sisb}}", "S", "p", 40, "v", 1, "L", "p", 0, "v", 0);
	json_t *dark = json_pack("{sisb}", "p", 0, "v", 0);
	gint64 now = janus_get_monotonic_time();
	janus_mutex_lock(&l->mutex);
	json_t *f = janus_slvoice_dot_batch_for_listener_locked(room, l, batch, dark,
		janus_slvoice_vis_ok_locked(room, l->display, now), NULL);
	janus_mutex_unlock(&l->mutex);
	CHECK(f != NULL && json_object_get(f, "S") == NULL && json_object_get(f, "L") != NULL,
		"row 1: an unarmed listener's dots keep only its own entry");
	json_decref(f);
	janus_slvoice_send_join_backlog_locked(l, room);
	janus_mutex_unlock(&room->mutex);
	CHECK(!got(l, "S", "j"), "row 1: the backlog carries no row for the source");

	/* The pair rule: the listener armed, the source not. */
	drop(arm(3101, E1, 1, "L", NULL));
	CHECK(row_of(room, "L") == 4 && row_of(room, "S") == 1, "pair rule: L in row 4, S in row 1");
	run_ticks(room, &src, 1, 10);
	CHECK(l->last_mix_rms == 0.0, "pair rule: an armed listener does not hear an unarmed source");
	CHECK(room->vis_ws_listeners == 1 && room->vis_ws_pairs == 2, "pair rule: would_silence counts S and both pairs");
	CHECK(sender_pass(room) == 1 && !got(l, "S", "j"), "pair rule: the sender hides S and announces nothing");
	janus_mutex_lock(&room->mutex);
	GHashTable *hidden = g_hash_table_new(g_str_hash, g_str_equal);
	g_hash_table_add(hidden, (gpointer)"S");
	janus_mutex_lock(&l->mutex);
	f = janus_slvoice_dot_batch_for_listener_locked(room, l, batch, dark, TRUE, hidden);
	janus_mutex_unlock(&l->mutex);
	g_hash_table_destroy(hidden);
	janus_mutex_unlock(&room->mutex);
	CHECK(f != NULL && json_object_get(f, "S") == NULL && json_object_get(f, "L") != NULL, "pair rule: the unarmed source's dot is omitted");
	json_decref(f);

	/* Row 4: the source armed too. */
	drop(arm(3101, E1, 2, "S", NULL));
	CHECK(sender_pass(room) == 0 && got(l, "S", "j") && got(s, "L", "j"), "row 4: arming the source emits join presence both ways");
	run_ticks(room, &src, 1, 10);
	CHECK(l->last_mix_rms > 0.05, "row 4: an armed listener hears an armed source");
	CHECK(room->vis_ws_listeners == 0 && room->vis_ws_pairs == 0, "row 4: would_silence is 0");
	clear_sent();
	janus_mutex_lock(&room->mutex);
	janus_slvoice_send_join_backlog_locked(l, room);
	janus_mutex_unlock(&room->mutex);
	CHECK(got(l, "S", "j"), "row 4: the backlog carries the source's row");

	/* Row 3 by the window: confirm both, then let the window run out. */
	drop(heartbeat(E1, 3101, "{\"L\":1,\"S\":2}", FALSE));
	gint64 confirmed = fake_now_us;
	fake_now_us = confirmed + 8000 * 1000 - 500 * 1000;
	run_ticks(room, &src, 1, 3);
	CHECK(l->last_mix_rms > 0.05 && row_of(room, "L") == 4, "window: still audible at JS_VIS_STALE_MS - 500 ms");
	fake_now_us = confirmed + 8000 * 1000 + 100 * 1000;
	run_ticks(room, &src, 1, 3);
	CHECK(l->last_mix_rms == 0.0 && row_of(room, "L") == 3 && row_of(room, "S") == 3, "row 3: silent by JS_VIS_STALE_MS + 100 ms");
	clear_sent();
	sender_pass(room);
	CHECK(got(l, "S", "l") && got(s, "L", "l"), "row 3: going stale emits leave presence both ways");

	/* Recovery in the same epoch: heartbeats alone restore it (assertion 10). */
	json_t *r = heartbeat(E1, 3101, "{\"L\":1,\"S\":2}", FALSE);
	json_t *rr = room_reply(r, 3101);
	CHECK(is_status(rr, "ok") && json_array_size(json_object_get(rr, "stale_listeners")) == 0
		&& json_array_size(json_object_get(rr, "unarmed_listeners")) == 0, "recovery: a matching heartbeat reports nothing");
	drop(r);
	run_ticks(room, &src, 1, 5);
	CHECK(l->last_mix_rms > 0.05, "recovery: audible again without a new arming");

	/* Row 3 by the stale mark, one listener in a live room (assertion 14). */
	janus_slvoice_session *m = new_session("M");
	join(room, m, NULL);
	drop(arm(3101, E1, 3, "M", NULL));
	r = heartbeat(E1, 3101, "{\"L\":5,\"S\":2,\"M\":3}", FALSE);
	rr = room_reply(r, 3101);
	CHECK(list_has(rr, "stale_listeners", "L") && !list_has(rr, "stale_listeners", "M")
		&& json_array_size(json_object_get(rr, "stale_listeners")) == 1, "row 3: a generation above L's lists only L stale");
	drop(r);
	run_ticks(room, &src, 1, 5);
	CHECK(l->last_mix_rms == 0.0 && m->last_mix_rms > 0.05 && row_of(room, "L") == 3,
		"row 3: the stale listener is silent while M in the same room still hears the source");
	drop(arm(3101, E1, 4, "L", NULL));
	run_ticks(room, &src, 1, 5);
	CHECK(l->last_mix_rms > 0.05 && row_of(room, "L") == 4, "row 3: a replace for L restores it");

	/* A new-epoch heartbeat does not revalidate (assertion 11): every record is disarmed. */
	r = heartbeat(E2, 3101, "{\"L\":4,\"S\":2,\"M\":3}", FALSE);
	rr = room_reply(r, 3101);
	CHECK(list_has(rr, "unarmed_listeners", "L") && list_has(rr, "unarmed_listeners", "S") && list_has(rr, "unarmed_listeners", "M")
		&& !strcmp(str_of(rr, "authority_epoch"), E2), "new epoch: the heartbeat lists everyone unarmed and reports E2");
	drop(r);
	run_ticks(room, &src, 1, 5);
	CHECK(l->last_mix_rms == 0.0 && row_of(room, "L") == 1, "new epoch: L is silent (row 1) despite matching generations");
	drop(arm(3101, E2, 1, "L", "S"));
	run_ticks(room, &src, 1, 5);
	CHECK(l->last_mix_rms > 0.05, "new epoch: an arming replace in E2 makes L audible (assertion 12)");

	/* A recorder tap, or any peer with no display, is never armed (O-88: silent until armed). */
	CHECK(row_of(room, NULL) == 1, "a session without a display stands in row 1");

	json_decref(batch);
	json_decref(dark);
	free_session(m);
	free_session(l);
	free_session(s);
	slv_vis_fail_closed = FALSE;
}

/* §1.1 and §1.2 on batches, with fail-closed on, then the same out-of-order and base-mismatch batches in shadow mode. */
static void test_batch_rules(void) {
	slv_vis_fail_closed = TRUE;
	janus_slvoice_room *room = new_room(3201, TRUE);
	janus_slvoice_session *l = new_session("L"), *s = new_session("S");
	join(room, s, NULL);
	join(room, l, NULL);
	tone_src src = { s, 0 };
	tone_prime(&src);
	drop(arm(3201, E2, 1, "L", "S"));

	json_t *r = admin("{\"request\":\"peer_ctl_batch\",\"op\":\"replace\",\"room\":3201,\"excl\":{\"L\":[\"S\"]},"
		"\"room_epoch\":\"" E1 "\",\"policy_generation\":5}");
	CHECK(!strcmp(str_of(r, "slvoice"), "error") && !strcmp(str_of(r, "reason"), "stale_epoch") && is_status(r, "stale_epoch")
		&& !strcmp(str_of(r, "authority_epoch"), E2), "stale_epoch: a lower epoch while E2 is fresh is refused and reported");
	drop(r);
	CHECK(g_hash_table_size(l->excluded) == 0 && room->vis_auth_epoch == E2V, "stale_epoch: nothing applied, authority unchanged");
	r = heartbeat(E1, 3201, "{\"L\":1,\"S\":1}", FALSE);
	CHECK(is_status(room_reply(r, 3201), "stale_epoch") && room->vis_stale_epoch_rejects == 2, "stale_epoch: a lower-epoch heartbeat too");
	drop(r);

	drop(arm(3201, E2, 10, "L", NULL));
	r = admin("{\"request\":\"peer_ctl_batch\",\"op\":\"add\",\"room\":3201,\"excl\":{\"L\":[\"S\"]},"
		"\"room_epoch\":\"" E2 "\",\"policy_generation\":9,\"base\":{\"L\":10}}");
	CHECK(!strcmp(str_of(r, "reason"), "stale_generation") && json_integer_value(json_object_get(r, "policy_generation")) == 10,
		"out of order: an add at generation 9 after a replace at 10 is refused");
	drop(r);
	CHECK(g_hash_table_size(l->excluded) == 0 && room->vis_stale_generation_rejects == 1, "out of order: L's set is unchanged");

	r = admin("{\"request\":\"peer_ctl_batch\",\"op\":\"add\",\"room\":3201,\"excl\":{\"L\":[\"S\"]},"
		"\"room_epoch\":\"" E2 "\",\"policy_generation\":11,\"base\":{\"L\":3}}");
	CHECK(!strcmp(str_of(r, "slvoice"), "applied") && list_has(r, "stale_listeners", "L"), "base mismatch: L listed stale");
	drop(r);
	run_ticks(room, &src, 1, 5);
	CHECK(g_hash_table_size(l->excluded) == 0 && row_of(room, "L") == 3 && l->last_mix_rms == 0.0,
		"base mismatch: L's entry is not applied and L is silenced");
	drop(arm(3201, E2, 12, "L", NULL));
	r = admin("{\"request\":\"peer_ctl_batch\",\"op\":\"add\",\"room\":3201,\"excl\":{\"L\":[\"S\"]},"
		"\"room_epoch\":\"" E2 "\",\"policy_generation\":13,\"base\":{\"L\":12}}");
	CHECK(json_array_size(json_object_get(r, "stale_listeners")) == 0 && record_of(room, "L")->listener_gen == 13
		&& g_hash_table_contains(l->excluded, "S") && g_hash_table_contains(record_of(room, "L")->excl, "S"),
		"base match: the add is applied, advances listener_gen and the record's column");
	drop(r);
	run_ticks(room, &src, 1, 5);
	CHECK(l->last_mix_rms == 0.0 && row_of(room, "L") == 4, "base match: the exclusion silences S for an armed L");
	drop(admin("{\"request\":\"peer_ctl_batch\",\"op\":\"remove\",\"room\":3201,\"excl\":{\"L\":[\"S\"]},"
		"\"room_epoch\":\"" E2 "\",\"policy_generation\":14,\"base\":{\"L\":13}}"));
	run_ticks(room, &src, 1, 5);
	CHECK(g_hash_table_size(l->excluded) == 0 && l->last_mix_rms > 0.05, "base match: the remove restores S");

	/* Takeover: E2 goes quiet for longer than the window; E1 is adopted and everything is disarmed first. */
	fake_now_us += (gint64)(slv_vis_stale_ms + 1) * 1000;
	r = arm(3201, E1, 1, "L", NULL);
	CHECK(is_status(r, "ok") && !strcmp(str_of(r, "authority_epoch"), E1) && row_of(room, "L") == 4 && row_of(room, "S") == 1,
		"takeover: a lower epoch after the window is adopted, disarming S, and arms L");
	drop(r);

	/* Shadow mode: the same kinds of batch are applied exactly as before; only the reply and the counters differ. */
	slv_vis_fail_closed = FALSE;
	r = admin("{\"request\":\"peer_ctl_batch\",\"op\":\"add\",\"room\":3201,\"excl\":{\"L\":[\"S\"]},"
		"\"room_epoch\":\"" E1 "\",\"policy_generation\":1,\"base\":{\"L\":1}}");
	CHECK(!strcmp(str_of(r, "slvoice"), "applied") && json_is_true(json_object_get(r, "stale_generation"))
		&& g_hash_table_contains(l->excluded, "S") && record_of(room, "L")->listener_gen == 1
		&& record_of(room, "L")->excl != NULL && g_hash_table_size(record_of(room, "L")->excl) == 0,
		"shadow: an out-of-order add is applied as before; the record is unchanged");
	drop(r);
	r = admin("{\"request\":\"peer_ctl_batch\",\"op\":\"add\",\"room\":3201,\"excl\":{\"L\":[\"T\"]},"
		"\"room_epoch\":\"" E1 "\",\"policy_generation\":2,\"base\":{\"L\":7}}");
	CHECK(list_has(r, "stale_listeners", "L") && g_hash_table_contains(l->excluded, "T"),
		"shadow: a base-mismatch add is applied as before and L is reported stale");
	drop(r);
	r = admin("{\"request\":\"peer_ctl_batch\",\"op\":\"replace\",\"room\":3201,\"excl\":{\"L\":[]},"
		"\"room_epoch\":\"" E_LOWER "\",\"policy_generation\":1}");
	CHECK(is_status(r, "stale_epoch") && !strcmp(str_of(r, "slvoice"), "applied") && g_hash_table_size(l->excluded) == 0,
		"shadow: a stale_epoch replace is applied as before and reported stale_epoch");
	drop(r);

	free_session(l);
	free_session(s);
}

/* §3: confirm, omission, graceful stop, takeover after stop, unknown and undeclared rooms, the interval check. */
static void test_heartbeat(void) {
	slv_vis_fail_closed = TRUE;
	slv_vis_stale_ms = 8000;
	janus_slvoice_room *room = new_room(3301, TRUE);
	janus_slvoice_room *undeclared = new_room(3302, FALSE);
	janus_slvoice_session *l = new_session("L"), *s = new_session("S");
	join(room, s, NULL);
	join(room, l, NULL);
	tone_src src = { s, 0 };
	tone_prime(&src);
	drop(arm(3301, E1, 1, "L", "S"));

	json_t *r = heartbeat(E1, 3301, "{\"L\":1,\"S\":1}", FALSE);
	json_t *rr = room_reply(r, 3301);
	CHECK(!strcmp(str_of(r, "slvoice"), "heartbeat") && json_integer_value(json_object_get(r, "vis_protocol")) == 2
		&& !strcmp(str_of(r, "mixer_instance"), slv_mixer_instance), "heartbeat reply: slvoice heartbeat, vis_protocol 2, mixer_instance");
	CHECK(is_status(rr, "ok") && !strcmp(str_of(rr, "authority_epoch"), E1) && json_integer_value(json_object_get(rr, "policy_generation")) == 1,
		"heartbeat reply: status ok, authority_epoch, policy_generation echoes the highest applied");
	drop(r);
	json_t *r2 = heartbeat(E1, 3301, "{\"L\":1,\"S\":1}", FALSE);
	CHECK(!strcmp(str_of(r2, "mixer_instance"), slv_mixer_instance), "heartbeat reply: mixer_instance is stable across calls");
	drop(r2);

	drop(heartbeat(E1, 3301, "{\"S\":1}", FALSE));
	run_ticks(room, &src, 1, 3);
	CHECK(row_of(room, "L") == 1 && l->last_mix_rms == 0.0 && g_hash_table_size(room->vis_records) == 1,
		"omission: a heartbeat that omits armed L disarms and silences it (assertion 15)");
	drop(arm(3301, E1, 2, "L", NULL));
	run_ticks(room, &src, 1, 3);
	CHECK(l->last_mix_rms > 0.05, "omission: re-arming restores L");

	drop(heartbeat(E1, 3301, "{\"L\":2,\"S\":1}", TRUE));
	run_ticks(room, &src, 1, 3);
	CHECK(l->last_mix_rms == 0.0 && row_of(room, "L") == 3 && room->vis_auth_last_us == 0,
		"graceful stop: stopping silences the room within 3 ticks, without the window (assertion 22)");
	r = arm(3301, E_LOWER, 1, "L", "S");
	CHECK(is_status(r, "ok") && !strcmp(str_of(r, "authority_epoch"), E_LOWER), "graceful stop: a lower epoch may take over at once");
	drop(r);
	run_ticks(room, &src, 1, 3);
	CHECK(l->last_mix_rms > 0.05, "graceful stop: the new authority's arming restores audio");

	r = admin("{\"request\":\"peer_ctl_heartbeat\",\"room_epoch\":\"" E_LOWER "\",\"interval_ms\":1000,"
		"\"rooms\":{\"3302\":{\"policy_generation\":0,\"listeners\":{}},\"999999\":{\"policy_generation\":0,\"listeners\":{}},"
		"\"x\":{}}}");
	CHECK(is_status(room_reply(r, 3302), "undeclared_room") && is_status(room_reply(r, 999999), "unknown_room")
		&& json_object_get(json_object_get(r, "rooms"), "x") == NULL, "heartbeat: undeclared_room, unknown_room, and a non-room key ignored");
	drop(r);
	CHECK(undeclared->vis_heartbeats == 1 && undeclared->vis_auth_epoch == E_LOWERV, "heartbeat: an undeclared room still tracks the authority");

	r = admin("{\"request\":\"peer_ctl_heartbeat\",\"room_epoch\":\"xyz\",\"interval_ms\":1000,\"rooms\":{}}");
	CHECK(!strcmp(str_of(r, "reason"), "malformed") && json_integer_value(json_object_get(r, "vis_protocol")) == 2,
		"heartbeat: a malformed epoch is rejected, still advertising vis_protocol");
	drop(r);
	r = admin("{\"request\":\"peer_ctl_heartbeat\",\"room_epoch\":\"" E_LOWER "\",\"interval_ms\":1375,\"rooms\":{}}");
	CHECK(!strcmp(str_of(r, "slvoice"), "heartbeat"), "interval: 1375 ms fits an 8000 ms window (2 x 1375 + 5250 = 8000)");
	drop(r);
	r = admin("{\"request\":\"peer_ctl_heartbeat\",\"room_epoch\":\"" E_LOWER "\",\"interval_ms\":1376,\"rooms\":{}}");
	CHECK(!strcmp(str_of(r, "reason"), "interval_too_long") && json_integer_value(json_object_get(r, "stale_ms")) == 8000,
		"interval: 1376 ms breaks the constraint and is rejected");
	drop(r);
	slv_vis_stale_ms = 7250;
	r = admin("{\"request\":\"peer_ctl_heartbeat\",\"room_epoch\":\"" E_LOWER "\",\"interval_ms\":1001,\"rooms\":{}}");
	CHECK(!strcmp(str_of(r, "reason"), "interval_too_long"), "interval: with the clamped 7250 ms window, 1001 ms is rejected");
	drop(r);
	r = admin("{\"request\":\"peer_ctl_heartbeat\",\"room_epoch\":\"" E_LOWER "\",\"interval_ms\":1000,\"rooms\":{}}");
	CHECK(!strcmp(str_of(r, "slvoice"), "heartbeat"), "interval: the sim's 1000 ms fits the clamped window");
	drop(r);
	slv_vis_stale_ms = 8000;

	free_session(l);
	free_session(s);
	slv_vis_fail_closed = FALSE;
}

/* WORK 3: a room created without vis_authority behaves as today with fail-closed on: no arming, no heartbeat. */
static void test_undeclared_room_unaffected(void) {
	slv_vis_fail_closed = TRUE;
	janus_slvoice_room *room = new_room(3401, FALSE);
	janus_slvoice_session *l = new_session("L"), *s = new_session("S");
	clear_sent();
	join(room, s, NULL);
	json_t *roster = NULL;
	join(room, l, &roster);
	CHECK(roster_has(roster, "S") && got(s, "L", "j"), "undeclared: the roster lists the source and the join is announced");
	json_decref(roster);
	tone_src src = { s, 0 };
	tone_prime(&src);
	run_ticks(room, &src, 1, 10);
	CHECK(l->last_mix_rms > 0.05, "undeclared: an unarmed listener hears the source with fail-closed on");
	CHECK(!janus_slvoice_vis_enforced(room) && room->vis_ws_listeners == 0 && room->vis_ws_pairs == 0 && room->vis_ws_listener_ticks == 0,
		"undeclared: not enforced, and would_silence stays 0");
	janus_mutex_lock(&room->mutex);
	clear_sent();
	janus_slvoice_send_join_backlog_locked(l, room);
	janus_mutex_unlock(&room->mutex);
	CHECK(got(l, "S", "j"), "undeclared: the backlog carries the source");
	drop(admin("{\"request\":\"peer_ctl_batch\",\"op\":\"replace\",\"room\":3401,\"excl\":{\"L\":[\"S\"]}}"));
	run_ticks(room, &src, 1, 5);
	CHECK(l->last_mix_rms == 0.0, "undeclared: an exclusion still silences the source, as today");
	free_session(l);
	free_session(s);
	slv_vis_fail_closed = FALSE;
}

/* WORK 5 and 6: fail-closed off (the shipped default). A declared room whose listener is never armed hears, sees and is
 * sent exactly what the same room undeclared would, while would_silence counts it. */
static void test_shadow_mode(void) {
	slv_vis_fail_closed = FALSE;
	janus_slvoice_room *u = new_room(3501, FALSE);
	janus_slvoice_room *d = new_room(3502, TRUE);
	janus_slvoice_session *lu = new_session("L"), *su = new_session("S"), *ld = new_session("L"), *sd = new_session("S");
	clear_sent();
	join(u, su, NULL);
	join(d, sd, NULL);
	json_t *ru = NULL, *rd = NULL;
	join(u, lu, &ru);
	join(d, ld, &rd);
	CHECK(roster_has(ru, "S") && roster_has(rd, "S") && json_array_size(ru) == json_array_size(rd),
		"shadow: the unarmed joiner's roster is the undeclared room's");
	json_decref(ru);
	json_decref(rd);
	CHECK(got(sd, "L", "j") && !strcmp(sent_text(su), sent_text(sd)), "shadow: the join presence is byte-identical");
	tone_src tu = { su, 0 }, td = { sd, 0 };
	tone_prime(&tu);
	tone_prime(&td);
	for(int k = 0; k < 30; k++) {
		tone_feed(&tu, 1);
		tone_feed(&td, 1);
		janus_slvoice_room_tick(u);
		janus_slvoice_room_tick(d);
		fake_now_us += TICK_US;
	}
	CHECK(lu->last_mix_rms > 0.05 && lu->last_mix_rms == ld->last_mix_rms && lu->last_mix_rms_l == ld->last_mix_rms_l
		&& lu->frames_encoded == ld->frames_encoded && lu->frames_mixed == ld->frames_mixed,
		"shadow: an unarmed listener in a declared room hears exactly what it hears in an undeclared room");
	CHECK(d->vis_ws_listeners == 2 && d->vis_ws_pairs == 2 && d->vis_ws_listener_ticks == 60,
		"shadow: would_silence_listeners 2, would_silence_pairs 2, 30 ticks x 2 in the cumulative count");
	CHECK(u->vis_ws_listeners == 0 && u->vis_ws_pairs == 0 && u->vis_ws_listener_ticks == 0,
		"shadow: nothing is counted in the undeclared room");
	CHECK(!janus_slvoice_vis_enforced(d), "shadow: the declared room is not enforced, so the sender passes no filter");
	clear_sent();
	janus_mutex_lock(&u->mutex);
	janus_slvoice_send_join_backlog_locked(lu, u);
	janus_mutex_unlock(&u->mutex);
	janus_mutex_lock(&d->mutex);
	janus_slvoice_send_join_backlog_locked(ld, d);
	janus_mutex_unlock(&d->mutex);
	CHECK(got(ld, "S", "j") && !strcmp(sent_text(lu), sent_text(ld)), "shadow: the backlog is byte-identical");

	drop(arm(3502, E1, 1, "L", "S"));
	for(int k = 0; k < 10; k++) {
		tone_feed(&tu, 1);
		tone_feed(&td, 1);
		janus_slvoice_room_tick(u);
		janus_slvoice_room_tick(d);
		fake_now_us += TICK_US;
	}
	CHECK(lu->last_mix_rms == ld->last_mix_rms && lu->frames_encoded == ld->frames_encoded && d->vis_ws_listeners == 0
		&& d->vis_ws_pairs == 0 && d->vis_ws_listener_ticks == 60, "shadow: arming changes no audio and zeroes the gauges");

	/* An old-sim (unstamped) batch lands the same in both rooms, and the replies differ only in the room number. */
	json_t *ou = admin("{\"request\":\"peer_ctl_batch\",\"op\":\"replace\",\"room\":3501,\"excl\":{\"L\":[\"S\"]},\"mute\":{}}");
	json_t *od = admin("{\"request\":\"peer_ctl_batch\",\"op\":\"replace\",\"room\":3502,\"excl\":{\"L\":[\"S\"]},\"mute\":{}}");
	json_object_del(ou, "room");
	json_object_del(od, "room");
	CHECK(json_equal(ou, od) && json_object_get(ou, "status") == NULL, "shadow: an unstamped batch replies identically, with no status");
	drop(ou);
	drop(od);
	for(int k = 0; k < 5; k++) {
		tone_feed(&tu, 1);
		tone_feed(&td, 1);
		janus_slvoice_room_tick(u);
		janus_slvoice_room_tick(d);
		fake_now_us += TICK_US;
	}
	CHECK(lu->last_mix_rms == 0.0 && ld->last_mix_rms == 0.0, "shadow: the exclusion silences the source in both rooms");

	free_session(lu);
	free_session(su);
	free_session(ld);
	free_session(sd);
}

/* §2 pre-join arming, and §7.4 / open question 2: a rejoin takes the armed columns under fail-closed only. */
static void test_prejoin_and_rejoin(void) {
	slv_vis_fail_closed = TRUE;
	janus_slvoice_room *room = new_room(3601, TRUE);
	janus_slvoice_session *s = new_session("S");
	join(room, s, NULL);
	tone_src src = { s, 0 };
	tone_prime(&src);
	drop(arm(3601, E1, 1, "S", NULL));
	drop(arm(3601, E1, 2, "L", NULL));
	CHECK(row_of(room, "L") == 4, "pre-join: an empty arming replace for a listener not yet in the room is kept");
	janus_slvoice_session *l = new_session("L");
	join(room, l, NULL);
	CHECK(l->vis_ok_last, "pre-join: the joiner is armed from its join");
	run_ticks(room, &src, 1, 3);
	CHECK(l->last_mix_rms > 0.05, "pre-join: audible within 3 ticks of joining with no heartbeat (assertion 18)");

	/* The columns applied while L was present; L's session leaves (its sets reset) and a new one joins. */
	drop(admin("{\"request\":\"peer_ctl_batch\",\"op\":\"replace\",\"room\":3601,\"excl\":{\"L\":[\"S\"]},\"mute\":{\"L\":[]},"
		"\"room_epoch\":\"" E1 "\",\"policy_generation\":3}"));
	CHECK(g_hash_table_contains(l->excluded, "S"), "rejoin: the exclusion reached the present session");
	janus_slvoice_leave_room(l);
	janus_slvoice_session *l2 = new_session("L");
	join(room, l2, NULL);
	CHECK(g_hash_table_contains(l2->excluded, "S") && l2->vis_ok_last, "rejoin (fail-closed): the new session takes the authority's columns");
	run_ticks(room, &src, 1, 3);
	CHECK(l2->last_mix_rms == 0.0, "rejoin (fail-closed): so it does not hear the excluded source");
	janus_slvoice_leave_room(l2);

	slv_vis_fail_closed = FALSE;
	janus_slvoice_session *l3 = new_session("L");
	join(room, l3, NULL);
	CHECK(g_hash_table_size(l3->excluded) == 0, "rejoin (shadow): the new session starts empty, exactly as before");

	free_session(l);
	free_session(l2);
	free_session(l3);
	free_session(s);
}

/* WORK 4: the reply keys, to a stamped and an unstamped sender. */
static void test_replies(void) {
	slv_vis_fail_closed = FALSE;
	janus_slvoice_room *room = new_room(3701, TRUE);
	janus_slvoice_session *l = new_session("L");
	join(room, l, NULL);

	json_t *r = admin("{\"request\":\"peer_ctl_batch\",\"op\":\"replace\",\"room\":3701,\"excl\":{\"L\":[]},\"mute\":{\"L\":[]}}");
	void *it = json_object_iter(r);
	CHECK(it != NULL && !strcmp(json_object_iter_key(it), "slvoice") && !strcmp(str_of(r, "slvoice"), "applied"),
		"unstamped: slvoice applied is still the first key");
	CHECK(json_integer_value(json_object_get(r, "vis_protocol")) == 2 && !strcmp(str_of(r, "mixer_instance"), slv_mixer_instance)
		&& json_object_get(r, "status") == NULL && json_object_get(r, "authority_epoch") == NULL,
		"unstamped: vis_protocol 2 and mixer_instance advertised; no authority keys");
	drop(r);
	CHECK(row_of(room, "L") == 1 && g_hash_table_size(room->vis_records) == 0, "unstamped: a batch without room_epoch arms no one");

	r = admin("{\"request\":\"peer_ctl_batch\",\"op\":\"replace\",\"room\":999998,\"excl\":{\"L\":[]}}");
	CHECK(!strcmp(str_of(r, "reason"), "unknown_room") && json_object_get(r, "status") == NULL
		&& json_integer_value(json_object_get(r, "vis_protocol")) == 2, "unstamped unknown room: unknown_room, advertised, no status");
	drop(r);
	r = admin("{\"request\":\"peer_ctl_batch\",\"op\":\"replace\",\"room\":999998,\"excl\":{\"L\":[]},\"room_epoch\":\"" E1 "\",\"policy_generation\":1}");
	CHECK(!strcmp(str_of(r, "reason"), "unknown_room") && is_status(r, "unknown_room"), "stamped unknown room: status unknown_room");
	drop(r);
	r = admin("{\"request\":\"peer_ctl_batch\",\"room\":3701}");
	CHECK(!strcmp(str_of(r, "reason"), "malformed") && json_integer_value(json_object_get(r, "vis_protocol")) == 2, "malformed batch: advertised");
	drop(r);
	r = admin("{\"request\":\"peer_ctl_batch\",\"op\":\"add\",\"room\":3701}");
	CHECK(!strcmp(str_of(r, "slvoice"), "empty") && json_integer_value(json_object_get(r, "vis_protocol")) == 2, "empty batch: advertised");
	drop(r);
	r = admin("{\"request\":\"bogus\"}");
	CHECK(!strcmp(str_of(r, "reason"), "unknown_request") && json_object_get(r, "vis_protocol") == NULL, "an unknown request is answered as before");
	drop(r);

	drop(arm(3701, E1, 5, "L", NULL));
	r = arm(3701, E1, 7, "L", NULL);
	CHECK(is_status(r, "ok") && !strcmp(str_of(r, "authority_epoch"), E1) && json_integer_value(json_object_get(r, "policy_generation")) == 7
		&& json_array_size(json_object_get(r, "stale_listeners")) == 0 && json_array_size(json_object_get(r, "unarmed_listeners")) == 0,
		"stamped: status, authority_epoch, policy_generation (the highest applied) and both lists");
	drop(r);
	free_session(l);
}

/* Slice 0.7d (ledger O-95): a heartbeat and a batch are separate flights, so a heartbeat the sim BUILT before a batch
 * succeeded can ARRIVE after it. Two orderings, constructed rather than raced:
 *   A  a replace for L is applied at N+1, then a heartbeat built at N arrives naming L at N;
 *   B  an arming replace for a new listener L2 is applied, then a heartbeat built before L2 existed arrives omitting it.
 * The heartbeat carries "as_of", the highest policy_generation its sender had successfully sent to the room when it
 * built it. One below the room's applied generation is OUTDATED: it keeps the room live and its listeners map is not
 * evaluated. A heartbeat with no as_of (an older sim) keeps today's behaviour. */
static json_t *heartbeat_as_of(const char *epoch, guint64 room, int as_of, const char *listeners) {
	return admin("{\"request\":\"peer_ctl_heartbeat\",\"room_epoch\":\"%s\",\"interval_ms\":1000,\"rooms\":{\"%" G_GUINT64_FORMAT
		"\":{\"policy_generation\":%d,\"as_of\":%d,\"listeners\":%s}}}", epoch, room, as_of, as_of, listeners);
}

static void test_heartbeat_ordering(void) {
	slv_vis_fail_closed = TRUE;
	slv_vis_stale_ms = 8000;
	janus_slvoice_room *room = new_room(3401, TRUE);
	janus_slvoice_session *l = new_session("L"), *s = new_session("S"), *l2 = new_session("L2");
	join(room, s, NULL);
	join(room, l, NULL);
	tone_src src = { s, 0 };
	tone_prime(&src);
	drop(arm(3401, E1, 1, "L", "S"));
	drop(heartbeat_as_of(E1, 3401, 1, "{\"L\":1,\"S\":1}"));
	run_ticks(room, &src, 1, 5);
	CHECK(row_of(room, "L") == 4 && l->last_mix_rms > 0.05, "ordering: L armed at generation 1 hears S");

	/* A: the replace for L applies at 2; the heartbeat that arrives next was built at 1 and names L at 1. */
	drop(arm(3401, E1, 2, "L", NULL));
	guint64 beats = room->vis_heartbeats;
	json_t *r = heartbeat_as_of(E1, 3401, 1, "{\"L\":1,\"S\":1}");
	CHECK(!list_has(room_reply(r, 3401), "stale_listeners", "L"),
		"ordering A: a heartbeat built before L's replace (as_of 1 < applied 2) does not list L stale");
	drop(r);
	run_ticks(room, &src, 1, 5);
	CHECK(record_of(room, "L") != NULL && !record_of(room, "L")->stale && row_of(room, "L") == 4 && l->last_mix_rms > 0.05,
		"ordering A: L stays armed at row 4 and keeps hearing S");
	CHECK(room->vis_heartbeats == beats + 1 && room->vis_heartbeats_outdated == 1,
		"ordering A: the outdated heartbeat still counts for liveness, and heartbeats_outdated counts it");

	/* B: L2 joins and is armed at 3; the heartbeat that arrives next was built at 2, before L2 existed. */
	join(room, l2, NULL);
	drop(arm(3401, E1, 3, "L2", NULL));
	drop(heartbeat_as_of(E1, 3401, 2, "{\"L\":2,\"S\":1}"));
	run_ticks(room, &src, 1, 5);
	CHECK(record_of(room, "L2") != NULL && row_of(room, "L2") == 4 && l2->last_mix_rms > 0.05,
		"ordering B: a heartbeat built before L2's arming (as_of 2 < applied 3) does not disarm L2, which keeps hearing S");
	CHECK(room->vis_heartbeats_outdated == 2, "ordering B: counted as outdated");

	/* A current heartbeat is evaluated exactly as before: omission still disarms. */
	drop(heartbeat_as_of(E1, 3401, 3, "{\"L\":2,\"S\":1}"));
	run_ticks(room, &src, 1, 3);
	CHECK(row_of(room, "L2") == 1 && l2->last_mix_rms == 0.0 && room->vis_heartbeats_outdated == 2,
		"ordering: a heartbeat at the applied generation (as_of 3) is evaluated, and its omission disarms L2");

	/* An older sim sends no as_of: today's behaviour, a mismatched generation marks L stale. */
	drop(arm(3401, E1, 4, "L", NULL));
	r = heartbeat(E1, 3401, "{\"L\":3,\"S\":1}", FALSE);
	CHECK(list_has(room_reply(r, 3401), "stale_listeners", "L") && record_of(room, "L")->stale,
		"ordering: with no as_of the heartbeat is evaluated as before (older sim), so L at a lower generation goes stale");
	drop(r);

	free_session(l);
	free_session(l2);
	free_session(s);
}

int main(void) {
	rooms = g_hash_table_new_full(g_int64_hash, g_int64_equal, g_free, NULL);
	sessions = g_hash_table_new(NULL, NULL);
	g_atomic_int_set(&initialized, 1);
	sent = g_hash_table_new_full(NULL, NULL, NULL, sent_free);
	gateway = &fake_gateway;
	gen_tone();
	fake_now_us = G_GINT64_CONSTANT(1000000000000);

	test_pure_rules();
	test_knobs_and_clamp();
	test_keying();
	test_decision_table();
	test_batch_rules();
	test_heartbeat();
	test_heartbeat_ordering();
	test_undeclared_room_unaffected();
	test_shadow_mode();
	test_prejoin_and_rejoin();
	test_replies();

	gateway = NULL;
	janus_slvoice_teardown();
	g_hash_table_destroy(sent);
	printf("test_visauth: %d checks, %d failures\n", g_checks, g_failures);
	return g_failures == 0 ? 0 : 1;
}
