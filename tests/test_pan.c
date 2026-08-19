/*! \file    tests/test_pan.c
 * \author   Legion Voice Mixer project
 * \copyright GNU General Public License v3
 * \brief    Unit tests for the constant-power stereo pan law (src/mixer/pan.h)
 *
 * \details  Plain C, links only libm. Built and run by `make test`. Expected
 * values are derived from the pan law and the right-to-right convention pinned in
 * docs/voice/phase3b-design-brief.md Amendments 4 and 5 — NOT from what the code
 * produces. Verification for Phase 3b item 4 is numeric (no listening tests are
 * available), so these ARE the acceptance criteria. Exit status is non-zero on any
 * failure so `make test` / the Docker build fails loudly.
 */

#include "../src/mixer/pan.h"

#include <stdio.h>
#include <math.h>

#ifndef M_PI
#define M_PI   3.14159265358979323846
#endif
#ifndef M_PI_2
#define M_PI_2 1.57079632679489661923
#endif
#ifndef M_PI_4
#define M_PI_4 0.78539816339744830962
#endif

/* cos(pi/4) == sin(pi/4): the centred (ahead / behind) per-channel gain. */
#define CENTRE 0.70710678118654752

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

/* ---- ahead: centred, equal power in both channels ------------------------- */
static void test_ahead_centred(void) {
	printf("test_ahead_centred\n");
	float l = -1.0f, r = -1.0f;
	slv_pan(0.0, &l, &r);
	/* p = (sin0+1)/2 = 1/2 -> a = pi/4 -> gainL = gainR = cos(pi/4). */
	CHECK(approx(l, CENTRE), "ahead: left gain = cos(pi/4)");
	CHECK(approx(r, CENTRE), "ahead: right gain = sin(pi/4)");
	CHECK(approx(l, r), "ahead: centred (L == R)");
}

/* ---- hard right: all power in the right channel --------------------------- */
static void test_hard_right(void) {
	printf("test_hard_right\n");
	float l = -1.0f, r = -1.0f;
	slv_pan(M_PI_2, &l, &r);   /* az = +pi/2, listener's right (Amendment 4) */
	/* p = (sin(pi/2)+1)/2 = 1 -> a = pi/2 -> gainL = cos(pi/2) = 0, gainR = 1. */
	CHECK(approx(l, 0.0), "hard right: left gain 0");
	CHECK(approx(r, 1.0), "hard right: right gain 1");
}

/* ---- hard left: all power in the left channel ----------------------------- */
static void test_hard_left(void) {
	printf("test_hard_left\n");
	float l = -1.0f, r = -1.0f;
	slv_pan(-M_PI_2, &l, &r);   /* az = -pi/2, listener's left */
	/* p = (sin(-pi/2)+1)/2 = 0 -> a = 0 -> gainL = cos0 = 1, gainR = sin0 = 0. */
	CHECK(approx(l, 1.0), "hard left: left gain 1");
	CHECK(approx(r, 0.0), "hard left: right gain 0");
}

/* ---- behind: centred, NOT lateralised (Amendment 5, honest limit) --------- */
static void test_behind_centred(void) {
	printf("test_behind_centred\n");
	float l = -1.0f, r = -1.0f;
	slv_pan(M_PI, &l, &r);   /* directly behind: sin(pi) = 0 -> same as ahead */
	CHECK(approx(l, CENTRE), "behind: left gain = cos(pi/4)");
	CHECK(approx(r, CENTRE), "behind: right gain = sin(pi/4)");
	CHECK(approx(l, r), "behind: centred (L == R), amplitude pan cannot place front/back");
	/* -pi is the SAME direction as +pi and must map to the same place: the pan
	 * signal sin(az) is continuous across directly-behind (Amendment 5). */
	float l2 = -1.0f, r2 = -1.0f;
	slv_pan(-M_PI, &l2, &r2);
	CHECK(approx(l, l2), "behind: +pi and -pi map to the same left gain");
	CHECK(approx(r, r2), "behind: +pi and -pi map to the same right gain");
}

/* ---- partial pan follows the convention: right of centre favours right ---- */
static void test_partial_right_favours_right(void) {
	printf("test_partial_right_favours_right\n");
	float l = -1.0f, r = -1.0f;
	slv_pan(M_PI_4, &l, &r);   /* +45 deg: to the right, but not hard */
	CHECK(r > l, "azimuth right of centre -> right channel louder (Amendment 4)");
	CHECK(l > 0.0f && r < 1.0f, "partial pan: both channels present, neither extreme");
}

/* ---- constant power across a full sweep: gainL^2 + gainR^2 == 1 ----------- */
static void test_constant_power_sweep(void) {
	printf("test_constant_power_sweep\n");
	/* 33 azimuths spanning [-pi, pi], including the behind wrap at both ends. */
	int bad = 0;
	for(int k = 0; k <= 32; k++) {
		double az = -M_PI + (2.0 * M_PI) * (double)k / 32.0;
		float l = 0.0f, r = 0.0f;
		slv_pan(az, &l, &r);
		double power = (double)l * l + (double)r * r;
		if(!approx(power, 1.0))
			bad++;
	}
	CHECK(bad == 0, "constant power (L^2 + R^2 == 1) across the azimuth sweep");
}

int main(void) {
	printf("== test_pan ==\n");
	test_ahead_centred();
	test_hard_right();
	test_hard_left();
	test_behind_centred();
	test_partial_right_favours_right();
	test_constant_power_sweep();
	printf("\n%d checks, %d failures\n", g_checks, g_failures);
	if(g_failures) {
		fprintf(stderr, "FAILED\n");
		return 1;
	}
	printf("OK\n");
	return 0;
}
