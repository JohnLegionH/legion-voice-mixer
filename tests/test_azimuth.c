/*! \file    tests/test_azimuth.c
 * \author   Legion Voice Mixer project
 * \copyright GNU General Public License v3
 * \brief    Unit tests for the horizontal azimuth maths (src/mixer/azimuth.h)
 *
 * \details  Plain C, links only libm. Built and run by `make test`. Expected
 * values are derived from the geometry and the conventions pinned in
 * docs/voice/phase3b-design-brief.md Amendment 4 — NOT from what the code
 * produces. Verification for Phase 3b item 4 is numeric (no listening tests are
 * available), so these ARE the acceptance criteria. Exit status is non-zero on
 * any failure so `make test` / the Docker build fails loudly.
 */

#include "../src/mixer/azimuth.h"

#include <stdio.h>
#include <math.h>

#ifndef M_PI
#define M_PI   3.14159265358979323846
#endif
#ifndef M_PI_2
#define M_PI_2 1.57079632679489661923
#endif

/* ---- Pan sign convention (Amendment 4), asserted explicitly ---------------
 * DECIDED in Amendment 4: a source to the listener's RIGHT is louder in the
 * RIGHT output channel. This file encodes that as the azimuth sign:
 *   azimuth POSITIVE = to the right, NEGATIVE = to the left, 0 = directly ahead.
 * So hard right = +pi/2 and hard left = -pi/2. Flip this one constant if the
 * convention is ever re-pinned; every left/right assertion follows from it. */
#define AZ_RIGHT_SIGN (+1.0)   /* sign of the azimuth for a hard-right source */

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

static slv_vec3 v3(double x, double y, double z) { slv_vec3 v = { x, y, z }; return v; }
static slv_quat q4(double x, double y, double z, double w) { slv_quat q = { x, y, z, w }; return q; }

static const slv_quat IDENTITY = { 0.0, 0.0, 0.0, 1.0 };   /* faces +X (east) */
/* 90 deg CCW about +Z (positive rotation per Amendment 4): forward +X -> +Y (north). */
#define S45 0.70710678118654752
static const slv_quat YAW90 = { 0.0, 0.0, S45, S45 };

/* ---- directly ahead: source on the listener's forward axis ---------------- */
static void test_ahead(void) {
	printf("test_ahead\n");
	/* Facing east; source due east is directly ahead. */
	double az = slv_azimuth(v3(0,0,0), IDENTITY, v3(10,0,0));
	CHECK(approx(az, 0.0), "source directly ahead -> azimuth 0");
}

/* ---- directly behind: assert magnitude pi, not sign ----------------------- */
static void test_behind(void) {
	printf("test_behind\n");
	/* Facing east; source due west is directly behind. Behind is neither left
	 * nor right, so only the magnitude is meaningful. */
	double az = slv_azimuth(v3(0,0,0), IDENTITY, v3(-10,0,0));
	CHECK(approx(fabs(az), M_PI), "source directly behind -> |azimuth| = pi");
}

/* ---- hard left / hard right: right = +pi/2, and right == -left ------------- */
static void test_hard_left_right(void) {
	printf("test_hard_left_right\n");
	/* Facing east (+X): the listener's right is south (-Y), left is north (+Y). */
	double right = slv_azimuth(v3(0,0,0), IDENTITY, v3(0,-10,0));   /* south = hard right */
	double left  = slv_azimuth(v3(0,0,0), IDENTITY, v3(0, 10,0));   /* north = hard left  */
	CHECK(approx(right, AZ_RIGHT_SIGN * M_PI_2), "hard right -> +pi/2");
	CHECK(approx(left, -AZ_RIGHT_SIGN * M_PI_2), "hard left -> -pi/2");
	CHECK(approx(right, -left), "hard right is the exact negation of hard left");
}

/* ---- listener rotated 90 deg about Z: the heading is applied --------------- */
static void test_rotated_listener(void) {
	printf("test_rotated_listener\n");
	/* Listener now faces +Y (north). */
	double ahead = slv_azimuth(v3(0,0,0), YAW90, v3(0,10,0));   /* north = now directly ahead */
	CHECK(approx(ahead, 0.0), "rotated listener: due-north source is ahead");
	double right = slv_azimuth(v3(0,0,0), YAW90, v3(10,0,0));   /* east = now to the right */
	CHECK(approx(right, AZ_RIGHT_SIGN * M_PI_2), "rotated listener: east source is hard right");
}

/* ---- wire-realistic truncated quaternion: must normalize ------------------ */
static void test_truncated_quat(void) {
	printf("test_truncated_quat\n");
	/* The same 90-deg-about-Z rotation as it arrives on the wire: (value x100,
	 * int-truncated). {0,0,70,70}, magnitude ~99 — NOT a unit quaternion
	 * (Amendment 4). Without normalization this silently produces a wrong angle. */
	slv_quat wire = q4(0, 0, 70, 70);
	double ahead = slv_azimuth(v3(0,0,0), wire, v3(0,10,0));
	double right = slv_azimuth(v3(0,0,0), wire, v3(10,0,0));
	CHECK(approx(ahead, 0.0), "truncated quat: north source still ahead (normalized)");
	CHECK(approx(right, AZ_RIGHT_SIGN * M_PI_2), "truncated quat: east source still hard right");
	/* Identical to the exact unit-quaternion case. */
	CHECK(approx(ahead, slv_azimuth(v3(0,0,0), YAW90, v3(0,10,0))), "truncated == unit result (ahead)");
	CHECK(approx(right, slv_azimuth(v3(0,0,0), YAW90, v3(10,0,0))), "truncated == unit result (right)");
}

/* ---- degenerate: overhead source, and the zero quaternion ----------------- */
static void test_degenerate(void) {
	printf("test_degenerate\n");
	double up = slv_azimuth(v3(0,0,0), IDENTITY, v3(0,0,10));   /* directly overhead */
	CHECK(!isnan(up), "overhead source -> not NaN");
	CHECK(approx(up, 0.0), "overhead source -> defined default 0");
	/* A zero-magnitude quaternion falls back to identity (forward +X). */
	double zero = slv_azimuth(v3(0,0,0), q4(0,0,0,0), v3(10,0,0));
	CHECK(!isnan(zero), "zero quaternion -> not NaN");
	CHECK(approx(zero, 0.0), "zero quaternion -> identity, east source ahead");
}

/* ---- pitch independence: azimuth depends on heading, not pitch ------------- */
static void test_pitch_independent(void) {
	printf("test_pitch_independent\n");
	/* Pitch up 30 deg about the local +Y (north) axis while facing east: forward
	 * tilts toward +Z but its horizontal heading stays +X. Quaternion for +30 deg
	 * about +Y is {0, sin15, 0, cos15}. */
	double s15 = sin(M_PI / 12.0), c15 = cos(M_PI / 12.0);
	slv_quat pitched = q4(0.0, s15, 0.0, c15);
	double az_flat    = slv_azimuth(v3(0,0,0), IDENTITY, v3(0,-10,0));   /* hard right, level */
	double az_pitched = slv_azimuth(v3(0,0,0), pitched, v3(0,-10,0));    /* same source, pitched */
	CHECK(approx(az_pitched, az_flat), "pitch does not change azimuth (heading only)");
}

int main(void) {
	printf("== test_azimuth ==\n");
	test_ahead();
	test_behind();
	test_hard_left_right();
	test_rotated_listener();
	test_truncated_quat();
	test_degenerate();
	test_pitch_independent();
	printf("\n%d checks, %d failures\n", g_checks, g_failures);
	if(g_failures) {
		fprintf(stderr, "FAILED\n");
		return 1;
	}
	printf("OK\n");
	return 0;
}
