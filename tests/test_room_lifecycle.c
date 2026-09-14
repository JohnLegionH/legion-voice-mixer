/*! \file    tests/test_room_lifecycle.c
 * \author   Legion Voice Mixer project
 * \copyright GNU General Public License v3
 * \brief    Unit tests for the mixer room lifecycle (slice 5): O-68 reset_room_state,
 *           O-54 empty-room grace destroy, O-56 hangup_media leaves the room, and the
 *           O-67 shared global teardown.
 *
 * \details  Reaches the plugin's static room/session functions the way tests/bench_tick.c
 * does: by #including the plugin source and defining the Janus core symbols the .so leaves
 * undefined. Linked like bench_tick with -Wl,--unresolved-symbols=ignore-all (janus_config_*
 * and janus_sdp_*, reached only from init and negotiate, neither of which runs here). Rooms
 * are real: room_create starts a real 20 ms tick thread and the teardown joins it. Test
 * sessions never set webrtc_up, so the tick's pass 2 (encode/relay) skips them. Built and run
 * by `make test`, so a failure fails the image build.
 *
 * NOT covered here: O-67's room_start / init thread-creation failure (no seam to make
 * g_thread_try_new fail), and the join arm clearing empty_since (it needs negotiate). Both are
 * covered by code review and the live check only.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

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
gint64 janus_get_monotonic_time(void) { return g_get_monotonic_time(); }
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
		fprintf(stderr, "  FAIL: %s\n", (msg)); \
	} \
} while(0)

#define FAR_FUTURE (G_MAXINT64 / 2)

static void noop_free(const janus_refcount *r) { (void)r; }

static janus_slvoice_session *make_session(void) {
	janus_slvoice_session *s = g_malloc0(sizeof(janus_slvoice_session));
	s->opus_pt = -1;
	s->excluded = g_hash_table_new_full(g_str_hash, g_str_equal, g_free, NULL);
	s->mod_muted = g_hash_table_new_full(g_str_hash, g_str_equal, g_free, NULL);
	janus_mutex_init(&s->mutex);
	janus_refcount_init(&s->ref, noop_free);
	return s;
}

static void free_session(janus_slvoice_session *s) {
	janus_mutex_lock(&s->mutex);
	janus_slvoice_media_free_locked(s);
	janus_mutex_unlock(&s->mutex);
	g_hash_table_destroy(s->excluded);
	g_hash_table_destroy(s->mod_muted);
	g_free(s->display);
	g_free(s);
}

/* Put s into room as a participant holding the room ref a joined session holds, and clear the
 * grace clock exactly as the handler's join arm does. */
static void join_room(janus_slvoice_room *room, janus_slvoice_session *s, guint64 uid) {
	janus_refcount_increase(&room->ref);
	janus_mutex_lock(&s->mutex);
	s->user_id = uid;
	s->room = room;
	janus_mutex_unlock(&s->mutex);
	janus_mutex_lock(&room->mutex);
	guint64 *key = g_malloc(sizeof(guint64));
	*key = uid;
	g_hash_table_insert(room->participants, key, s);
	room->empty_since = 0;
	janus_mutex_unlock(&room->mutex);
}

static janus_slvoice_room *add_room_ex(guint64 id, gboolean permanent, gboolean spatial) {
	janus_slvoice_room *room = janus_slvoice_room_create(id, NULL, FALSE, 48000, spatial, permanent);
	if(room == NULL) {
		fprintf(stderr, "test_room_lifecycle: room_create(%" PRIu64 ") failed\n", id);
		exit(2);
	}
	janus_mutex_lock(&rooms_mutex);
	guint64 *key = g_malloc(sizeof(guint64));
	*key = id;
	g_hash_table_insert(rooms, key, room);
	janus_mutex_unlock(&rooms_mutex);
	return room;
}

static janus_slvoice_room *add_room(guint64 id, gboolean permanent) {
	return add_room_ex(id, permanent, FALSE);
}

static gboolean room_present(guint64 id) {
	janus_mutex_lock(&rooms_mutex);
	gboolean present = g_hash_table_lookup(rooms, &id) != NULL;
	janus_mutex_unlock(&rooms_mutex);
	return present;
}

static guint sweep(gint64 now) {
	janus_mutex_lock(&rooms_mutex);
	guint n = janus_slvoice_sweep_empty_rooms_locked(now);
	janus_mutex_unlock(&rooms_mutex);
	return n;
}

static gint64 empty_since_of(janus_slvoice_room *room) {
	janus_mutex_lock(&room->mutex);
	gint64 since = room->empty_since;
	janus_mutex_unlock(&room->mutex);
	return since;
}

/* O-68: room-scoped state is cleared, viewer-personal state is kept. */
static void test_reset_room_state(void) {
	janus_slvoice_session *s = make_session();
	g_hash_table_add(s->excluded, g_strdup("src-a"));
	g_hash_table_insert(s->mod_muted, g_strdup("src-b"), GINT_TO_POINTER(1));
	g_strlcpy(s->cull_hyst[0].uuid, "src-c", SLV_UUID_LEN);
	s->cull_hyst[0].culled = TRUE;
	s->n_cull_hyst = 1;
	s->snap_valid = TRUE;
	s->snap_sp.x = 1.0;
	s->snap_lp.y = 2.0;
	s->last_data.sp.x = 100.0;
	s->last_data.sh.w = 100.0;
	s->last_data.lp.z = 300.0;
	s->last_data.lh.w = 100.0;
	s->last_data.m = 1;
	s->last_data.echo = 1;
	s->last_data_fields = SLV_FIELD_J | SLV_FIELD_SP | SLV_FIELD_SH | SLV_FIELD_LP | SLV_FIELD_LH
		| SLV_FIELD_M | SLV_FIELD_UG | SLV_FIELD_ECHO;
	s->last_msg_fields = SLV_FIELD_SP | SLV_FIELD_LP;
	g_atomic_int_set(&s->backlog_confirmed, 1);
	g_strlcpy(s->peer_ctl[0].uuid, "src-d", SLV_UUID_LEN);
	s->peer_ctl[0].muted = TRUE;
	s->peer_ctl[0].has_gain = TRUE;
	s->peer_ctl[0].gain = 0.5f;
	s->n_peer_ctl = 1;
	g_atomic_int_set(&s->echo_active, 1);

	janus_mutex_lock(&s->mutex);
	janus_slvoice_reset_room_state_locked(s);
	janus_mutex_unlock(&s->mutex);

	unsigned geo = SLV_FIELD_J | SLV_FIELD_L | SLV_FIELD_SP | SLV_FIELD_SH | SLV_FIELD_LP | SLV_FIELD_LH;
	unsigned own = SLV_FIELD_M | SLV_FIELD_UG | SLV_FIELD_ECHO;
	CHECK(s->excluded != NULL && g_hash_table_size(s->excluded) == 0, "reset empties excluded and keeps the table");
	CHECK(s->mod_muted != NULL && g_hash_table_size(s->mod_muted) == 0, "reset empties mod_muted and keeps the table");
	CHECK(s->n_cull_hyst == 0 && s->cull_hyst[0].uuid[0] == '\0' && !s->cull_hyst[0].culled, "reset clears the cull hysteresis");
	CHECK(!s->snap_valid && s->snap_sp.x == 0.0 && s->snap_lp.y == 0.0, "reset clears the geometry snapshot");
	CHECK(s->last_data.sp.x == 0.0 && s->last_data.sh.w == 0.0 && s->last_data.lp.z == 0.0 && s->last_data.lh.w == 0.0,
		"reset clears the last_data geometry");
	CHECK((s->last_data_fields & geo) == 0, "reset clears the geometry and presence field bits");
	CHECK(s->last_msg_fields == 0, "reset clears last_msg_fields");
	CHECK(g_atomic_int_get(&s->backlog_confirmed) == 0, "reset clears backlog_confirmed");
	CHECK((s->last_data_fields & own) == own && s->last_data.m == 1 && s->last_data.echo == 1,
		"reset keeps the viewer's own m/ug/echo");
	CHECK(s->n_peer_ctl == 1 && s->peer_ctl[0].muted && s->peer_ctl[0].has_gain && s->peer_ctl[0].gain == 0.5f,
		"reset keeps peer_ctl (viewer-personal)");
	CHECK(g_atomic_int_get(&s->echo_active) == 1, "reset keeps the echo setting");
	free_session(s);
}

/* O-54: grace clock, sweep, permanent/occupied exemptions, the shared teardown, the 0 knob. */
static void test_grace_destroy(void) {
	gint64 grace = (gint64)slv_empty_room_grace_s * G_USEC_PER_SEC;
	CHECK(slv_empty_room_grace_s == SLV_EMPTY_ROOM_GRACE_S && SLV_EMPTY_ROOM_GRACE_S == 60, "compiled grace default is 60 s");

	janus_slvoice_room *r = add_room(1001, FALSE);
	gint64 since = empty_since_of(r);
	CHECK(since != 0, "a non-permanent room is created with the grace clock running");
	CHECK(sweep(since + grace - 1) == 0 && room_present(1001), "an empty room inside the grace is kept");
	CHECK(sweep(since + grace) == 1 && !room_present(1001), "an empty room at the grace is destroyed and removed");

	janus_slvoice_room *p = add_room(1002, TRUE);
	CHECK(empty_since_of(p) == 0, "a permanent room has no grace clock");
	janus_mutex_lock(&p->mutex);
	p->empty_since = 1;
	janus_mutex_unlock(&p->mutex);
	CHECK(sweep(FAR_FUTURE) == 0 && room_present(1002), "a permanent room is never grace-destroyed");

	janus_slvoice_room *o = add_room(1003, FALSE);
	janus_slvoice_session *a = make_session();
	join_room(o, a, 7);
	janus_mutex_lock(&a->mutex);
	g_hash_table_add(a->excluded, g_strdup("src-x"));
	janus_mutex_unlock(&a->mutex);
	CHECK(empty_since_of(o) == 0, "a join clears the grace clock");
	CHECK(sweep(FAR_FUTURE) == 0 && room_present(1003), "an occupied room is never grace-destroyed");
	janus_slvoice_leave_room(a);
	gint64 osince = empty_since_of(o);
	janus_mutex_lock(&o->mutex);
	guint opop = g_hash_table_size(o->participants);
	janus_mutex_unlock(&o->mutex);
	CHECK(opop == 0 && osince != 0, "the last leave starts the grace clock");
	CHECK(a->room == NULL && g_hash_table_size(a->excluded) == 0, "leave_room resets the leaver's room-scoped state");
	CHECK(sweep(osince + grace - 1) == 0 && room_present(1003), "the emptied room is kept inside the grace");
	CHECK(sweep(osince + grace) == 1 && !room_present(1003), "the emptied room is destroyed at the grace");

	/* The explicit "destroy" request's path: evicts a straggler and resets it (O-68 at :1955). */
	janus_slvoice_room *d = add_room(1004, FALSE);
	janus_slvoice_session *b = make_session();
	join_room(d, b, 8);
	janus_mutex_lock(&b->mutex);
	g_hash_table_add(b->excluded, g_strdup("src-y"));
	g_hash_table_insert(b->mod_muted, g_strdup("src-y"), GINT_TO_POINTER(1));
	janus_mutex_unlock(&b->mutex);
	g_atomic_int_set(&b->backlog_confirmed, 1);
	janus_mutex_lock(&rooms_mutex);
	janus_slvoice_room_teardown_locked(d);
	janus_mutex_unlock(&rooms_mutex);
	janus_refcount_decrease(&d->ref);
	CHECK(!room_present(1004), "teardown removes the room from rooms");
	CHECK(b->room == NULL && g_hash_table_size(b->excluded) == 0 && g_hash_table_size(b->mod_muted) == 0
		&& g_atomic_int_get(&b->backlog_confirmed) == 0,
		"teardown evicts a straggler and resets its room-scoped state");

	guint saved = slv_empty_room_grace_s;
	slv_empty_room_grace_s = 0;
	add_room(1005, FALSE);
	CHECK(sweep(FAR_FUTURE) == 0 && room_present(1005), "grace 0 disables the grace destroy");
	slv_empty_room_grace_s = saved;
	CHECK(sweep(FAR_FUTURE) == 1 && !room_present(1005) && room_present(1002),
		"with the grace restored only the empty non-permanent room goes");

	free_session(a);
	free_session(b);
}

/* O-56: a downed PeerConnection leaves the room, frees media, resets jitter-buffer priming. */
static void test_hangup_leaves_room(void) {
	janus_slvoice_room *h = add_room(1006, FALSE);
	janus_slvoice_session *s = make_session();
	janus_plugin_session *handle = g_malloc0(sizeof(janus_plugin_session));
	handle->plugin_handle = s;
	s->handle = handle;
	join_room(h, s, 9);
	janus_mutex_lock(&s->mutex);
	gboolean alloc = janus_slvoice_media_alloc_locked(s);
	s->jb_have_first = TRUE;
	s->jb_primed = TRUE;
	s->jb_next = 42;
	s->jb_newest = 45;
	s->jb_first_seq = 40;
	s->last_rtp_us = janus_get_monotonic_time();
	g_hash_table_add(s->excluded, g_strdup("src-z"));
	g_strlcpy(s->peer_ctl[0].uuid, "src-p", SLV_UUID_LEN);
	s->peer_ctl[0].muted = TRUE;
	s->n_peer_ctl = 1;
	janus_mutex_unlock(&s->mutex);
	g_atomic_int_set(&s->dc_open, 1);
	CHECK(alloc, "media alloc for the hangup case");

	janus_slvoice_hangup_media(handle);

	janus_mutex_lock(&h->mutex);
	guint hpop = g_hash_table_size(h->participants);
	janus_mutex_unlock(&h->mutex);
	CHECK(s->room == NULL && hpop == 0, "hangup_media removes the participant from its room");
	CHECK(empty_since_of(h) != 0, "hangup of the last participant starts the grace clock");
	CHECK(!s->media_ready && s->dec == NULL && s->enc == NULL && s->jb == NULL, "hangup_media frees the media");
	CHECK(!s->jb_have_first && !s->jb_primed && s->jb_next == 0 && s->jb_newest == 0 && s->jb_first_seq == 0
		&& s->last_rtp_us == 0, "hangup_media resets jitter-buffer priming");
	CHECK(g_hash_table_size(s->excluded) == 0, "hangup_media resets room-scoped state");
	CHECK(g_atomic_int_get(&s->dc_open) == 0 && g_atomic_int_get(&s->webrtc_up) == 0, "hangup_media drops dc_open / webrtc_up");
	CHECK(s->n_peer_ctl == 1 && s->peer_ctl[0].muted, "hangup_media keeps peer_ctl");

	janus_slvoice_hangup_media(handle);
	janus_slvoice_leave_room(s);
	CHECK(s->room == NULL && room_present(1006), "a repeated hangup and the later leave are no-ops");

	free_session(s);
	g_free(handle);
}

/* O-80: a non-spatial room is a flat mix (no cull, falloff or pan); a spatial room keeps all three. */
static void test_spatial_pair(void) {
	janus_slvoice_room *flat = add_room_ex(1101, FALSE, FALSE);
	janus_slvoice_room *spat = add_room_ex(1102, FALSE, TRUE);
	janus_slvoice_session *l = make_session();
	janus_slvoice_session *src = make_session();
	src->display = g_strdup("src-o80");
	l->snap_valid = TRUE;
	src->snap_valid = TRUE;
	l->snap_lh = (slv_quat){ 0.0, 0.0, 0.0, 1.0 };

	/* Beyond the cutoff. */
	src->snap_sp = (slv_vec3){ slv_spatial.cutoff_dist + 100.0, 0.0, 0.0 };
	float gl = -1.0f, gr = -1.0f;
	gboolean culled = janus_slvoice_spatial_pair_locked(flat, l, src, src->display, 0.5f, &gl, &gr);
	CHECK(!culled && gl == 0.5f && gr == 0.5f,
		"non-spatial room: a source beyond the cutoff is not culled and keeps its flat gain");
	CHECK(l->n_cull_hyst == 0, "non-spatial room: no cull hysteresis is written");
	culled = janus_slvoice_spatial_pair_locked(spat, l, src, src->display, 0.5f, &gl, &gr);
	CHECK(culled && l->n_cull_hyst == 1, "spatial room: the same source is culled");

	/* 35 m: inside the re-add distance, so the latch clears. Falloff t = (60 - 35) / 50 = 0.5,
	 * gain 0.5 * 0.5^2 = 0.125, split at constant power (L^2 + R^2 = gain^2). */
	src->snap_sp = (slv_vec3){ 35.0 * SLV_GEOM_SCALE, 0.0, 0.0 };
	culled = janus_slvoice_spatial_pair_locked(spat, l, src, src->display, 0.5f, &gl, &gr);
	CHECK(!culled && fabs((double)gl * gl + (double)gr * gr - 0.125 * 0.125) < 1e-6,
		"spatial room: an in-range source is attenuated by the falloff and panned at constant power");
	culled = janus_slvoice_spatial_pair_locked(flat, l, src, src->display, 0.5f, &gl, &gr);
	CHECK(!culled && gl == 0.5f && gr == 0.5f,
		"non-spatial room: the same in-range source is neither attenuated nor panned");

	free_session(l);
	free_session(src);
	/* flat and spat stay in rooms; the global teardown in main frees them. */
}

/* O-83: absent spatial_audio means spatial; only an explicit false narrows to a flat mix. */
static void test_spatial_default(void) {
	CHECK(janus_slvoice_spatial_from_json(NULL), "create without spatial_audio: spatial");
	json_t *t = json_true(), *f = json_false(), *s = json_string("false"), *n = json_null();
	CHECK(janus_slvoice_spatial_from_json(t), "create with spatial_audio true: spatial");
	CHECK(!janus_slvoice_spatial_from_json(f), "create with spatial_audio false: flat");
	CHECK(janus_slvoice_spatial_from_json(s), "a non-boolean spatial_audio does not narrow");
	CHECK(janus_slvoice_spatial_from_json(n), "a null spatial_audio does not narrow");
	json_decref(t);
	json_decref(f);
	json_decref(s);
	json_decref(n);
	CHECK(janus_slvoice_spatial_from_cfg(NULL), "static room without the key: spatial");
	CHECK(janus_slvoice_spatial_from_cfg("true") && janus_slvoice_spatial_from_cfg("yes"), "static room true/yes: spatial");
	CHECK(!janus_slvoice_spatial_from_cfg("false") && !janus_slvoice_spatial_from_cfg("No"), "static room false/no: flat");
}

static json_int_t dot_p(json_t *batch, const char *key) {
	json_t *e = json_object_get(batch, key);
	return e != NULL ? json_integer_value(json_object_get(e, "p")) : -1;
}

static gboolean dot_v(json_t *batch, const char *key) {
	json_t *e = json_object_get(batch, key);
	return e != NULL && json_is_true(json_object_get(e, "v"));
}

/* SC-87: each listener's dot batch matches what it hears; culled and muted are checked separately. */
static void test_dot_batch_filter(void) {
	janus_slvoice_room *room = add_room_ex(1201, FALSE, TRUE);
	janus_slvoice_room_stop(room);   /* freeze tick_seq: this test sets it */
	janus_slvoice_session *l = make_session();
	janus_slvoice_session *far = make_session();
	far->display = g_strdup("src-culled");
	json_t *dark = json_pack("{sisb}", "p", 0, "v", 0);
	json_t *batch = json_pack("{s{sisb}s{sisb}s{sisb}s{sisb}s{sisb}}",
		"src-heard", "p", 40, "v", 1, "src-modmuted", "p", 40, "v", 1, "src-muted", "p", 40, "v", 1,
		"src-culled", "p", 40, "v", 1, "src-excluded", "p", 40, "v", 1);

	CHECK(janus_slvoice_dot_batch_for_listener_locked(room, l, batch, dark) == NULL,
		"dots: nothing excluded, muted or culled, so the listener gets the shared batch");

	/* Moderation mute on its own. */
	g_hash_table_add(l->mod_muted, g_strdup("src-modmuted"));
	json_t *f = janus_slvoice_dot_batch_for_listener_locked(room, l, batch, dark);
	CHECK(f != NULL && dot_p(f, "src-modmuted") == 0 && !dot_v(f, "src-modmuted"),
		"dots: a moderation-muted source reports no power to that listener");
	CHECK(f != NULL && dot_p(f, "src-heard") == 40 && dot_v(f, "src-heard") && dot_p(f, "src-culled") == 40,
		"dots: the other sources are unchanged beside a moderation mute");
	CHECK(dot_p(batch, "src-modmuted") == 40 && dot_v(batch, "src-modmuted"), "dots: the shared batch is not modified");
	json_decref(f);
	g_hash_table_remove_all(l->mod_muted);

	/* Distance cull on its own: the tick at seq 7 culls the far source for l. */
	l->snap_valid = TRUE;
	far->snap_valid = TRUE;
	l->snap_lh = (slv_quat){ 0.0, 0.0, 0.0, 1.0 };
	far->snap_sp = (slv_vec3){ slv_spatial.cutoff_dist + 100.0, 0.0, 0.0 };
	room->tick_seq = 7;
	float gl = 0.0f, gr = 0.0f;
	CHECK(janus_slvoice_spatial_pair_locked(room, l, far, far->display, 1.0f, &gl, &gr), "dots: the far source is culled at tick 7");
	f = janus_slvoice_dot_batch_for_listener_locked(room, l, batch, dark);
	CHECK(f != NULL && dot_p(f, "src-culled") == 0 && !dot_v(f, "src-culled"),
		"dots: a source distance-culled for that listener reports no power to it");
	CHECK(f != NULL && dot_p(f, "src-heard") == 40 && dot_p(f, "src-modmuted") == 40,
		"dots: the other sources are unchanged beside a cull");
	json_decref(f);
	room->tick_seq = 8;
	CHECK(janus_slvoice_dot_batch_for_listener_locked(room, l, batch, dark) == NULL,
		"dots: a cull latch the last tick did not re-evaluate does not darken the dot");

	/* A personal mute darkens; an exclusion still omits. */
	g_strlcpy(l->peer_ctl[0].uuid, "src-muted", SLV_UUID_LEN);
	l->peer_ctl[0].muted = TRUE;
	l->n_peer_ctl = 1;
	g_hash_table_add(l->excluded, g_strdup("src-excluded"));
	f = janus_slvoice_dot_batch_for_listener_locked(room, l, batch, dark);
	CHECK(f != NULL && dot_p(f, "src-muted") == 0 && !dot_v(f, "src-muted"),
		"dots: a source the listener muted reports no power to it");
	CHECK(f != NULL && json_object_get(f, "src-excluded") == NULL && dot_p(f, "src-heard") == 40,
		"dots: an excluded source is omitted and a heard source is unchanged");
	json_decref(f);

	json_decref(batch);
	json_decref(dark);
	free_session(l);
	free_session(far);
}

int main(void) {
	rooms = g_hash_table_new_full(g_int64_hash, g_int64_equal, g_free, NULL);
	sessions = g_hash_table_new(NULL, NULL);
	g_atomic_int_set(&initialized, 1);

	test_reset_room_state();
	test_grace_destroy();
	test_hangup_leaves_room();
	test_spatial_pair();
	test_spatial_default();
	test_dot_batch_filter();

	/* O-67: the shared global teardown (destroy() and a failed init) with no worker threads
	 * started and rooms still live (1002 permanent, 1006 empty) leaves nothing behind. */
	janus_slvoice_teardown();
	CHECK(rooms == NULL && sessions == NULL && messages == NULL
		&& !g_atomic_int_get(&initialized) && !g_atomic_int_get(&stopping),
		"global teardown with no worker threads frees all state and resets the flags");

	printf("test_room_lifecycle: %d checks, %d failures\n", g_checks, g_failures);
	return g_failures == 0 ? 0 : 1;
}
