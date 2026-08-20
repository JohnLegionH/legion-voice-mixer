/*! \file    tests/bench_tick.c
 * \author   Legion Voice Mixer project
 * \copyright GNU General Public License v3
 * \brief    Tick benchmark harness — measures janus_slvoice_room_tick wall time
 *           at a chosen occupancy (N participants, A active talkers).
 *
 * \details  Per docs/voice/scaling-assessment.md, the pinned measurement is the
 * per-tick cost at N≈110. This harness reaches the tick by #including the plugin
 * source (the room/session structs and the tick are static), provides the Janus
 * core symbols the plugin leaves undefined (the core supplies them in production),
 * builds one room and N synthetic sessions WITHOUT a real Janus transport, makes A
 * of them active+decodable by priming their jitter buffers with pre-encoded Opus
 * frames, and drives janus_slvoice_room_tick in a timed loop.
 *
 * It is a BENCHMARK, not a unit test: it is NOT wired into `make test` and never
 * runs on a normal build. Build with `make bench_tick`; run `tests/bench_tick [N]
 * [A] [iters]`. It reports total per-tick wall time (external clock) alongside the
 * tick's own histogram, so the numbers line up with what a live mixer reports.
 *
 * Composition (encode vs setup vs decode) is obtained SEPARATELY by profiling this
 * binary (callgrind ratios × the absolute total here); this file adds NO timers to
 * the tick — it only times room_tick as a unit.
 *
 * Every way this harness differs from the real tick is listed at the bottom of the
 * file (search "HARNESS DIFFERENCES") and echoed at startup.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <math.h>

/* Reach the plugin's static room/session/tick by compiling it into this TU. Its
 * own quoted includes ("sldata.h", "mixer/mix.h") resolve relative to src/.
 *
 * LINKAGE — read before extending this harness. Compiling the whole plugin pulls in
 * two families of Janus core symbols the harness never calls:
 *   - janus_config_*  (janus_config_parse/print/get/get_categories/destroy) — from
 *     janus_slvoice_init;
 *   - janus_sdp_*     (janus_sdp_parse/get_codec_pt/mline_find/generate_answer/
 *     generate_answer_mline/attribute_create/attribute_add_to_mline/write/destroy) —
 *     from janus_slvoice_negotiate.
 * Both init and negotiate are on the join/config paths, which this harness does NOT
 * exercise (it builds sessions directly). Rather than stub all 14 with fragile
 * signature-matched no-ops, the Makefile links with
 * -Wl,--unresolved-symbols=ignore-all. CONSEQUENCE: those symbols are left undefined,
 * so a future harness change that reaches init/negotiate/handle_message will
 * SEGFAULT at runtime (calling a null address) rather than fail to link. If you
 * extend this to touch those paths, add real stubs (or link the real Janus) first. */
#include "../src/janus_slvoice.c"

/* ---- Janus core symbols the plugin references but the .so leaves undefined
 *      (resolved from the core process in production). Defined here so the harness
 *      links. mutex/refcount are header macros over pthread/atomics and need only
 *      the two debug-flag globals below. ---- */
int      janus_log_level      = 0;      /* 0 => the JANUS_LOG guard fails => silent */
gboolean janus_log_timestamps = FALSE;
gboolean janus_log_colors     = FALSE;
char    *janus_log_global_prefix = NULL;
int      lock_debug           = 0;      /* janus_mutex_* take the nodebug fast path */
int      refcount_debug       = 0;      /* janus_refcount_* take the nodebug fast path */

/* JANUS_PRINT expands to janus_vprintf; the JANUS_LOG macro references it even when
 * the level guard fails, so the symbol must exist. Silent — logging is off. */
void janus_vprintf(const char *format, ...) { (void)format; }
/* Referenced by janus_slvoice_incoming_rtp, which the harness never calls (sources
 * are primed via jb_insert directly), but the symbol is compiled in. */
char *janus_rtp_payload(char *buf, int len, int *plen) { (void)buf; (void)len; if(plen) *plen = 0; return NULL; }

gint64 janus_get_monotonic_time(void) { return g_get_monotonic_time(); }

/* Deterministic LCG — values feed user_id/ssrc/seq only, none of which affect cost. */
static guint64 s_rng = 0x9e3779b97f4a7c15ULL;
guint64 janus_random_uint64(void) { s_rng = s_rng * 6364136223846793005ULL + 1442695040888963407ULL; return s_rng; }

/* Never reached on the tick path (handle_message only); stubbed so the TU links. */
janus_plugin_result *janus_plugin_result_new(janus_plugin_result_type type, const char *text, json_t *content) {
	(void)type; (void)text; (void)content; return NULL;
}
void janus_plugin_rtp_extensions_reset(janus_plugin_rtp_extensions *extensions) { (void)extensions; }

/* No-op gateway: encode still runs; the relay is discarded (no transport, no send). */
static void bench_relay_rtp(janus_plugin_session *h, janus_plugin_rtp *p)   { (void)h; (void)p; }
static void bench_relay_data(janus_plugin_session *h, janus_plugin_data *p)  { (void)h; (void)p; }
static int  bench_push_event(janus_plugin_session *h, janus_plugin *pl, const char *tx, json_t *m, json_t *j) {
	(void)h; (void)pl; (void)tx; (void)m; (void)j; return 0;
}
static janus_callbacks bench_gateway = {
	.relay_rtp  = bench_relay_rtp,
	.relay_data = bench_relay_data,
	.push_event = bench_push_event,
};

static void bench_noop_free(const janus_refcount *r) { (void)r; }

/* ---- Synthetic Opus source frames ---------------------------------------- */
#define BENCH_NFRAMES 8
static unsigned char bench_frame[BENCH_NFRAMES][SLV_JB_PAYLOAD_MAX];
static int           bench_frame_len[BENCH_NFRAMES];

/* Encode BENCH_NFRAMES of a 440 Hz sine (amplitude 0.5, stereo) at the plugin's
 * bitrate. Amplitude 0.5 => decoded RMS ~0.35 >> SLV_VAD_RMS (0.02), so a decoded
 * source counts as active/audible and every listener's mix is non-silent (encode
 * runs). */
static void bench_gen_frames(void) {
	int err = 0;
	OpusEncoder *enc = opus_encoder_create(SLV_RATE, SLV_CHANNELS, OPUS_APPLICATION_VOIP, &err);
	if(err != OPUS_OK || enc == NULL) { fprintf(stderr, "bench: opus_encoder_create failed\n"); exit(2); }
	opus_encoder_ctl(enc, OPUS_SET_BITRATE(SLV_OPUS_BITRATE));
	opus_encoder_ctl(enc, OPUS_SET_COMPLEXITY(SLV_OPUS_COMPLEXITY));
	static float pcm[SLV_FRAME_SAMPLES * SLV_CHANNELS];
	for(int f = 0; f < BENCH_NFRAMES; f++) {
		for(int n = 0; n < SLV_FRAME_SAMPLES; n++) {
			double t = (double)(f * SLV_FRAME_SAMPLES + n) / (double)SLV_RATE;
			float v = 0.5f * (float)sin(2.0 * M_PI * 440.0 * t);
			pcm[2 * n]     = v;
			pcm[2 * n + 1] = v;
		}
		int len = opus_encode_float(enc, pcm, SLV_FRAME_SAMPLES, bench_frame[f], SLV_JB_PAYLOAD_MAX);
		if(len < 0) { fprintf(stderr, "bench: opus_encode_float failed: %s\n", opus_strerror(len)); exit(2); }
		bench_frame_len[f] = len;
	}
	opus_encoder_destroy(enc);
}

/* ---- Geometry modes ------------------------------------------------------- */
typedef enum { GEO_CLUSTER = 0, GEO_SPREAD = 1 } bench_geo;
#define BENCH_REGION_M 256.0   /* one SL region footprint (x100 units => 25600) */

/* ---- Session construction ------------------------------------------------- */
static janus_slvoice_session *bench_make_session(janus_slvoice_room *room, int i, int n, bench_geo mode) {
	janus_slvoice_session *s = g_malloc0(sizeof(janus_slvoice_session));
	s->handle  = (janus_plugin_session *)(uintptr_t)(0x1000 + i);   /* non-NULL sentinel; never dereferenced */
	s->user_id = (guint64)(i + 1);
	s->display = g_strdup_printf("%08x-0000-4000-8000-000000000000", (unsigned)(i + 1));
	s->room    = room;
	janus_mutex_init(&s->mutex);
	janus_refcount_init(&s->ref, bench_noop_free);

	janus_mutex_lock(&s->mutex);
	if(!janus_slvoice_media_alloc_locked(s)) { fprintf(stderr, "bench: media_alloc failed\n"); exit(2); }
	janus_mutex_unlock(&s->mutex);
	g_atomic_int_set(&s->webrtc_up, 1);   /* pass-2 listener gate (setup_media in prod) */

	/* Geometry. Camera at the avatar (lp=sp, so no §7.1 leash effect); identity
	 * heading (azimuth then varies with relative position). x100 units throughout. */
	if(mode == GEO_SPREAD) {
		/* Distribute across one 256x256 m region on a near-square row-major grid, so
		 * a realistic fraction of listener-source pairs exceed the 60 m cull cutoff
		 * (a 60 m radius is ~17% of the region area). A far listener's talkers get
		 * culled or attenuated below the silence floor, so its mix goes silent and
		 * encode is skipped — the whole point of this mode. */
		int cols = (int)ceil(sqrt((double)n));
		if(cols < 1) cols = 1;
		double spacing = BENCH_REGION_M / (double)cols;              /* metres/cell */
		double x_m = (double)(i % cols) * spacing + 0.5 * spacing;
		double y_m = (double)(i / cols) * spacing + 0.5 * spacing;
		s->last_data.sp.x = x_m * 100.0; s->last_data.sp.y = y_m * 100.0; s->last_data.sp.z = 0.0;
	} else {
		/* CLUSTER (default, reproduces prior numbers): a 10 m-radius ring, all inside
		 * the cutoff, so no pair is culled — every pair runs azimuth+pan (max setup)
		 * and every listener hears every active source (max encode). */
		double th = 2.0 * M_PI * (double)i / (double)n;
		double r  = 1000.0;
		s->last_data.sp.x = r * cos(th); s->last_data.sp.y = r * sin(th); s->last_data.sp.z = 0.0;
	}
	s->last_data.lp   = s->last_data.sp;
	s->last_data.lh.x = 0.0; s->last_data.lh.y = 0.0; s->last_data.lh.z = 0.0; s->last_data.lh.w = 100.0;
	s->last_data_fields = SLV_FIELD_SP | SLV_FIELD_LP | SLV_FIELD_LH;

	guint64 *key = g_malloc(sizeof(guint64));
	*key = s->user_id;
	g_hash_table_insert(room->participants, key, s);
	return s;
}

static int cmp_double(const void *a, const void *b) {
	double x = *(const double *)a, y = *(const double *)b;
	return (x < y) ? -1 : (x > y) ? 1 : 0;
}

int main(int argc, char **argv) {
	int N     = (argc > 1) ? atoi(argv[1]) : 8;
	int A     = (argc > 2) ? atoi(argv[2]) : 4;
	int iters = (argc > 3) ? atoi(argv[3]) : 500;
	/* argv[4]: geometry mode, default "cluster" so prior numbers stay reproducible. */
	bench_geo mode = (argc > 4 && strcmp(argv[4], "spread") == 0) ? GEO_SPREAD : GEO_CLUSTER;
	if(N < 1) N = 1;
	if(A < 0) A = 0;
	if(A > N) A = N;
	if(iters < 1) iters = 1;
	if(N > SLV_MAX_MIX)
		fprintf(stderr, "bench: N=%d exceeds SLV_MAX_MIX=%d; the tick will mix only the first %d "
			"(rebuild with -DSLV_MAX_MIX=%d)\n", N, SLV_MAX_MIX, SLV_MAX_MIX, N);

	gateway = &bench_gateway;               /* the plugin's static gateway pointer */
	bench_gen_frames();

	/* room_create UNCONDITIONALLY starts the ticker thread (janus_slvoice.c:477);
	 * quiesce it immediately, then drive room_tick synchronously ourselves. */
	janus_slvoice_room *room = janus_slvoice_room_create(1, "bench", FALSE, SLV_RATE, TRUE, TRUE);
	janus_slvoice_room_stop(room);

	janus_slvoice_session **sess = g_malloc0((gsize)N * sizeof(*sess));
	for(int i = 0; i < N; i++)
		sess[i] = bench_make_session(room, i, N, mode);

	/* The A talkers are evenly index-strided, so in spread mode they land on distinct
	 * grid rows across the region (distributed "crowd chatter"), NOT clustered in one
	 * spot. Chosen deliberately: distributed talkers put more listeners near SOME
	 * talker than a clustered panel would, so this is the CONSERVATIVE spread case
	 * (more listeners encode); a panel-in-a-corner would encode even fewer. In cluster
	 * mode all are inside the cutoff, so the choice does not change the result. */
	int *talkers = g_malloc0((gsize)(A > 0 ? A : 1) * sizeof(int));
	for(int k = 0; k < A; k++)
		talkers[k] = (int)(((long)k * (long)N) / (long)A);

	/* Prime each talker: enough increasing-seq frames to cross the fixed lag so
	 * jb_pull yields SLV_JB_FRAME on the first measured tick. */
	for(int k = 0; k < A; k++) {
		janus_slvoice_session *ts = sess[talkers[k]];
		janus_mutex_lock(&ts->mutex);
		for(int seq = 0; seq <= SLV_JB_LAG + 1; seq++)
			janus_slvoice_jb_insert(ts, (uint16_t)seq, bench_frame[seq % BENCH_NFRAMES], bench_frame_len[seq % BENCH_NFRAMES]);
		janus_mutex_unlock(&ts->mutex);
	}
	uint16_t next_seq = (uint16_t)(SLV_JB_LAG + 2);

	/* Feed one frame + refresh liveness for the active set, then tick. */
	const int WARMUP = (iters > 20) ? 10 : 1;
	double *samples = g_malloc0((gsize)iters * sizeof(double));
	int nsamp = 0;
	for(int it = 0; it < iters + WARMUP; it++) {
		/* At the warmup boundary, zero each session's real-encode counter so the sum
		 * after the loop is exactly the encodes over the `iters` measured ticks. This
		 * reads/writes a session field the harness owns — NO production code changes. */
		if(it == WARMUP)
			for(int i = 0; i < N; i++) sess[i]->frames_encoded = 0;
		gint64 now = janus_get_monotonic_time();
		for(int k = 0; k < A; k++) {
			janus_slvoice_session *ts = sess[talkers[k]];
			janus_mutex_lock(&ts->mutex);
			ts->last_rtp_us = now;   /* keep in the active set (<=150ms) */
			janus_slvoice_jb_insert(ts, next_seq, bench_frame[next_seq % BENCH_NFRAMES], bench_frame_len[next_seq % BENCH_NFRAMES]);
			janus_mutex_unlock(&ts->mutex);
		}
		next_seq++;
		gint64 t0 = janus_get_monotonic_time();
		janus_slvoice_room_tick(room);
		gint64 t1 = janus_get_monotonic_time();
		if(it >= WARMUP)
			samples[nsamp++] = (double)(t1 - t0) / 1000.0;   /* ms */
	}
	/* frames_encoded (guint64, janus_slvoice.c:396) is incremented in encode_relay
	 * only on a real Opus packet (silence/DTX skips it), so the sum is the true
	 * per-tick encode count — the O(N) cost centre made visible. */
	guint64 total_enc = 0;
	for(int i = 0; i < N; i++) total_enc += sess[i]->frames_encoded;
	double enc_per_tick = (double)total_enc / (double)nsamp;

	/* Stats over the measured ticks. */
	double sum = 0.0, mn = 1e18, mx = 0.0;
	for(int i = 0; i < nsamp; i++) { sum += samples[i]; if(samples[i] < mn) mn = samples[i]; if(samples[i] > mx) mx = samples[i]; }
	qsort(samples, nsamp, sizeof(double), cmp_double);
	double p50 = samples[nsamp / 2];
	double p95 = samples[(nsamp * 95) / 100];
	double mean = sum / (double)nsamp;

	printf("== bench_tick ==\n");
	printf("build: SLV_MAX_MIX=%d SLV_OPUS_COMPLEXITY=%d SLV_OPUS_BITRATE=%d\n",
		SLV_MAX_MIX, SLV_OPUS_COMPLEXITY, SLV_OPUS_BITRATE);
	printf("load:  N=%d listeners, A=%d active talkers, iters=%d (+%d warmup), geometry=%s\n",
		N, A, iters, WARMUP, mode == GEO_SPREAD ? "spread(256x256m grid)" : "cluster(10m ring)");
	printf("encodes: %.1f of %d listeners encoded per tick (real Opus packets; silent/culled mixes skipped)\n",
		enc_per_tick, N);
	printf("tick wall time (external clock), ms:\n");
	printf("  min=%.3f  p50=%.3f  mean=%.3f  p95=%.3f  max=%.3f\n", mn, p50, mean, p95, mx);
	printf("  20ms budget: p50 %s, p95 %s, max %s\n",
		p50 <= 20.0 ? "OK" : "OVER", p95 <= 20.0 ? "OK" : "OVER", mx <= 20.0 ? "OK" : "OVER");
	printf("tick's own last-tick ms (matches live mixer report): %.3f  max=%.3f\n",
		room->tick_last_ms, room->tick_max_ms);
	printf("tick histogram [<5 / 5-10 / 10-15 / 15-20 / >=20 ms]: %" G_GUINT64_FORMAT
		" %" G_GUINT64_FORMAT " %" G_GUINT64_FORMAT " %" G_GUINT64_FORMAT " %" G_GUINT64_FORMAT
		"   overruns(>%.0fms)=%" G_GUINT64_FORMAT "\n",
		room->tick_hist[0], room->tick_hist[1], room->tick_hist[2], room->tick_hist[3], room->tick_hist[4],
		SLV_TICK_WARN_MS, room->tick_overruns);
	return 0;
}

/* ============================================================================
 * HARNESS DIFFERENCES — every way this differs from the production tick.
 *
 *  1. room_create (janus_slvoice.c:477) unconditionally starts the ticker thread;
 *     the harness room_stops it immediately and drives room_tick synchronously on
 *     the main thread. A ticker thread briefly ran once, on the empty room.
 *  2. No Janus transport: relay_rtp/relay_data/push_event are no-ops. Opus encode
 *     runs and its output is discarded — the real relay_rtp's UDP/SRTP send cost is
 *     NOT measured (it is not part of the tick's CPU composition anyway).
 *  3. Sources are primed via jb_insert + last_rtp_us set directly, not through
 *     incoming_rtp. incoming_rtp runs on a different thread in production, not in
 *     the tick; the jitter-buffer state and the decode path exercised are identical.
 *  4. Single-threaded: room->mutex and every s->mutex are always uncontended. The
 *     real tick holds room->mutex the whole duration against concurrent
 *     incoming_rtp/join/SLData threads; that contention/wait time is NOT measured.
 *  5. Synthetic audio: sources decode a repeating 440 Hz sine (amp 0.5), not speech.
 *     Decode CPU is representative but content-dependent; source frames use the
 *     plugin's bitrate/complexity.
 *  6. Geometry is selectable (argv[4]). CLUSTER (default): a static ring inside the
 *     cutoff, NO pair culled — every pair runs azimuth+pan (max setup) and every
 *     listener mixes every active source (max encode). This is the UPPER BOUND.
 *     SPREAD: a 256x256 m grid, so far pairs are culled/attenuated to silence and
 *     their encode is skipped — the tick's real silent-mix skip, so spread is MORE
 *     representative, not less. Either way positions never move, so the cull
 *     hysteresis never flaps and there is no VAD/join churn (see #7).
 *  7. Fixed active set: A talkers, no VAD churn, no join/leave, no DTX transitions;
 *     the decode count is constant tick to tick.
 *  8. Warm cache / steady state: repeated identical ticks; real ticks compete with
 *     other regions and subsystems for cache and cores.
 *  9. Logging silenced (janus_log_level=0): the plugin's own JANUS_LOG calls (e.g.
 *     the tick-overrun WARN) are suppressed; negligible, but noted.
 * 10. janus_random_uint64 is a deterministic LCG and janus_get_monotonic_time wraps
 *     g_get_monotonic_time (real); neither affects tick cost.
 * ==========================================================================*/
