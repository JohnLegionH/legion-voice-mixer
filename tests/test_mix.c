/*! \file    tests/test_mix.c
 * \author   Legion Voice Mixer project
 * \copyright GNU General Public License v3
 * \brief    Unit tests for the pure N-minus-one mixing math (src/mixer/mix.c).
 *
 * \details  Plain C, links only libm (no Janus, no Opus, no glib). Built and
 * run by `make test`. Covers: N-minus-one summing (self excluded), active-set
 * gating, per-source mute application, per-source and master gain, clamp, RMS,
 * and the DTX/encode-skip silence test. Exit status is non-zero on any failure
 * so CI / the Docker build fails loudly.
 */

#include "../src/mixer/mix.h"

#include <stdio.h>
#include <string.h>
#include <math.h>

static int g_failures = 0;
static int g_checks = 0;

#define CHECK(cond, msg) do { \
	g_checks++; \
	if(!(cond)) { \
		g_failures++; \
		fprintf(stderr, "  FAIL: %s (%s:%d)\n", (msg), __FILE__, __LINE__); \
	} \
} while(0)

static int approx(double a, double b) { return fabs(a - b) < 1e-6; }

/* Small helper: fill a buffer with a constant. */
static void fill(float *b, size_t n, float v) { for(size_t i = 0; i < n; i++) b[i] = v; }

/* ---- N-minus-one: the listener never hears itself ------------------------- */
static void test_nminus1_excludes_self(void) {
	printf("test_nminus1_excludes_self\n");
	enum { N = 8 };
	float s0[N], s1[N], s2[N], out[N];
	fill(s0, N, 0.1f); fill(s1, N, 0.2f); fill(s2, N, 0.4f);
	const float *srcs[3] = { s0, s1, s2 };
	int active[3] = { 1, 1, 1 };
	/* Listener is source 1: mix = s0 + s2 = 0.5, NOT including its own 0.2 */
	int summed = slv_mix_nminus1(out, N, srcs, active, NULL, NULL, 3, 1);
	CHECK(summed == 2, "two other sources summed");
	CHECK(approx(out[0], 0.5f), "self excluded from own mix");
	/* Listener 0: mix = s1 + s2 = 0.6 */
	summed = slv_mix_nminus1(out, N, srcs, active, NULL, NULL, 3, 0);
	CHECK(summed == 2, "listener0 sums two");
	CHECK(approx(out[0], 0.6f), "listener0 mix value");
}

/* ---- active-set gating: inactive sources contribute nothing --------------- */
static void test_active_gating(void) {
	printf("test_active_gating\n");
	enum { N = 4 };
	float s0[N], s1[N], out[N];
	fill(s0, N, 0.3f); fill(s1, N, 0.5f);
	const float *srcs[2] = { s0, s1 };
	int active[2] = { 1, 0 };   /* s1 culled (DTX/inactive) */
	int summed = slv_mix_nminus1(out, N, srcs, active, NULL, NULL, 2, -1);
	CHECK(summed == 1, "only the active source summed");
	CHECK(approx(out[0], 0.3f), "inactive source omitted");
	/* A NULL source pointer is treated as absent even if flagged active. */
	const float *srcs2[2] = { s0, NULL };
	int active2[2] = { 1, 1 };
	summed = slv_mix_nminus1(out, N, srcs2, active2, NULL, NULL, 2, -1);
	CHECK(summed == 1, "NULL source skipped");
}

/* ---- per-source mute: "mute A in B's viewer silences A for B only" -------- */
static void test_per_source_mute(void) {
	printf("test_per_source_mute\n");
	enum { N = 4 };
	float a[N], c[N], out[N];
	fill(a, N, 0.4f); fill(c, N, 0.25f);
	const float *srcs[2] = { a, c };   /* source 0 = A, source 1 = C */
	int active[2] = { 1, 1 };
	int mutes[2] = { 1, 0 };           /* this listener muted A only */
	int summed = slv_mix_nminus1(out, N, srcs, active, mutes, NULL, 2, -1);
	CHECK(summed == 1, "muted source excluded, others remain");
	CHECK(approx(out[0], 0.25f), "A silenced for this listener, C audible");
	/* With only A present and A muted, the mix is fully silent. */
	int mutesAll[1] = { 1 };
	int active1[1] = { 1 };
	summed = slv_mix_nminus1(out, N, srcs, active1, mutesAll, NULL, 1, -1);
	CHECK(summed == 0, "two-party: muting the only peer yields silence");
	CHECK(slv_mix_is_silent(out, N, 1e-6), "silent mix after mute");
}

/* ---- per-source gain (ug/220) and master gain application ----------------- */
static void test_gain(void) {
	printf("test_gain\n");
	enum { N = 4 };
	float a[N], c[N], out[N];
	fill(a, N, 0.4f); fill(c, N, 0.4f);
	const float *srcs[2] = { a, c };
	int active[2] = { 1, 1 };
	/* Halve A, leave C at unity: 0.4*0.5 + 0.4*1.0 = 0.6 */
	float gains[2] = { 0.5f, 1.0f };
	int summed = slv_mix_nminus1(out, N, srcs, active, NULL, gains, 2, -1);
	CHECK(summed == 2, "both summed with gains");
	CHECK(approx(out[0], 0.6f), "per-source gain applied");
	/* A zero per-source gain drops the source entirely (like a mute). */
	float gains0[2] = { 0.0f, 1.0f };
	summed = slv_mix_nminus1(out, N, srcs, active, NULL, gains0, 2, -1);
	CHECK(summed == 1, "zero-gain source not counted");
	CHECK(approx(out[0], 0.4f), "zero-gain source contributes nothing");
	/* Master gain over the whole mix. */
	fill(out, N, 0.8f);
	slv_mix_apply_gain(out, N, 0.5f);
	CHECK(approx(out[0], 0.4f), "master gain halves the mix");
}

/* ---- clamp guards the encoder from summed overflow ------------------------ */
static void test_clamp(void) {
	printf("test_clamp\n");
	enum { N = 4 };
	float b[N];
	b[0] = 1.7f; b[1] = -2.0f; b[2] = 0.5f; b[3] = -0.5f;
	slv_mix_clamp(b, N);
	CHECK(approx(b[0], 1.0f), "positive overshoot clamped");
	CHECK(approx(b[1], -1.0f), "negative overshoot clamped");
	CHECK(approx(b[2], 0.5f), "in-range positive untouched");
	CHECK(approx(b[3], -0.5f), "in-range negative untouched");
}

/* ---- RMS + DTX silence detection ------------------------------------------ */
static void test_rms_silence(void) {
	printf("test_rms_silence\n");
	enum { N = 6 };
	float z[N];
	fill(z, N, 0.0f);
	CHECK(approx(slv_mix_rms(z, N), 0.0), "silence RMS is 0");
	CHECK(slv_mix_is_silent(z, N, 0.02), "silence is silent");
	float c[N];
	fill(c, N, 0.5f);   /* constant 0.5 -> RMS 0.5 */
	CHECK(approx(slv_mix_rms(c, N), 0.5), "constant RMS");
	CHECK(!slv_mix_is_silent(c, N, 0.02), "loud is not silent");
	/* A tiny residual below threshold is treated as silence (encode-skip). */
	float tiny[N];
	fill(tiny, N, 0.001f);
	CHECK(slv_mix_is_silent(tiny, N, 0.02), "sub-threshold treated as silent");
	CHECK(slv_mix_rms(NULL, 0) == 0.0, "empty buffer RMS is 0");
}

/* ---- stereo path: per-channel gains, and wrapper equivalence (Amendment 5) -
 * New in Phase 3b item 4. Does NOT touch the 26 checks above. Verifies (a) that
 * the stereo core pans (gainL != gainR -> L != R in the interleaved output), and
 * (b) that slv_mix_nminus1 is a faithful thin wrapper: identical output to the
 * stereo core called with gainsL == gainsR == gains. */
static void test_stereo_path(void) {
	printf("test_stereo_path\n");
	enum { N = 8 };   /* 4 stereo frames */
	float s0[N], out[N];
	fill(s0, N, 0.5f);
	const float *srcs[1] = { s0 };
	int active[1] = { 1 };
	/* Pan toward the right: gL != gR must produce L != R, per channel. */
	float gL[1] = { 0.25f };
	float gR[1] = { 1.0f };
	int summed = slv_mix_nminus1_stereo(out, N, srcs, active, NULL, gL, gR, 1, -1);
	CHECK(summed == 1, "stereo: one source summed");
	CHECK(approx(out[0], 0.125f), "stereo: even (left) sample scaled by gainL");
	CHECK(approx(out[1], 0.5f), "stereo: odd (right) sample scaled by gainR");
	CHECK(!approx(out[0], out[1]), "stereo: gainL != gainR yields L != R");

	/* Wrapper equivalence: run a non-trivial mix (self-exclusion + mute + varied
	 * gains) through both entry points; every sample must match. */
	enum { M = 8 };
	float a[M], b[M], c[M], ow[M], os[M];
	fill(a, M, 0.3f); fill(b, M, 0.2f); fill(c, M, 0.1f);
	const float *s3[3] = { a, b, c };
	int act3[3] = { 1, 1, 1 };
	int mut3[3] = { 0, 1, 0 };          /* listener muted source 1 */
	float g3[3] = { 0.5f, 1.0f, 0.75f };
	int sw = slv_mix_nminus1(ow, M, s3, act3, mut3, g3, 3, -1);
	int ss = slv_mix_nminus1_stereo(os, M, s3, act3, mut3, g3, g3, 3, -1);
	CHECK(sw == ss, "wrapper and stereo core sum the same source count");
	int identical = 1;
	for(int i = 0; i < M; i++)
		if(!approx(ow[i], os[i])) identical = 0;
	CHECK(identical, "wrapper output identical to stereo core with gainsL==gainsR");
}

int main(void) {
	printf("== test_mix ==\n");
	test_nminus1_excludes_self();
	test_active_gating();
	test_per_source_mute();
	test_gain();
	test_clamp();
	test_rms_silence();
	test_stereo_path();
	printf("\n%d checks, %d failures\n", g_checks, g_failures);
	if(g_failures) {
		fprintf(stderr, "FAILED\n");
		return 1;
	}
	printf("OK\n");
	return 0;
}
