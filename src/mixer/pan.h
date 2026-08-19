/*! \file    pan.h
 * \author   Legion Voice Mixer project
 * \copyright GNU General Public License v3
 * \brief    Constant-power stereo pan gains from a horizontal azimuth (Phase 3b item 4)
 *
 * \details  Pure math for amplitude panning: given the horizontal azimuth of a
 * source in the listener's head frame (radians; 0 = ahead, positive = to the
 * listener's RIGHT — the sign produced by \ref mixer/azimuth.h and pinned in
 * docs/voice/phase3b-design-brief.md Amendment 4), returns the per-channel linear
 * gains for a constant-power pan.
 *
 * Pan law (Amendment 5): the pan SIGNAL is sin(azimuth), not azimuth itself, so
 * it is continuous everywhere and needs no clamping — a source drifting across
 * directly-behind does not snap between pan extremes, and +pi and -pi (the same
 * direction) map to the same place. From that signal:
 *   p     = (sin(az) + 1) / 2      in [0,1]:  0 = hard left, 1 = hard right
 *   gainL = cos(p * pi/2)
 *   gainR = sin(p * pi/2)
 * so gainL^2 + gainR^2 = 1 (constant power) for every azimuth.
 *
 * Consequences that are CORRECT, not defects (Amendment 5):
 *   - Ahead (az=0):    sin=0  -> p=1/2 -> gainL=gainR=cos(pi/4) (centred).
 *   - Hard right (+pi/2): sin=+1 -> p=1 -> gainL=0, gainR=1 (full right).
 *   - Hard left (-pi/2):  sin=-1 -> p=0 -> gainL=1, gainR=0 (full left).
 *   - Behind (+/-pi):  sin=0  -> p=1/2 -> centred, full level. Amplitude panning
 *     cannot express front vs back; behind-renders-centred is the honest limit of
 *     the technique (front/back is what the deferred HRTF/ITD are for), NOT a bug.
 *
 * Header-only \c static \c inline, dependency-free beyond libm, matching
 * \ref mixer/azimuth.h — no translation unit, unit-tested via \c #include
 * (tests/test_pan.c). Verification is numeric (no listening tests available), so
 * those tests are the acceptance criteria.
 */

#ifndef SLV_PAN_H
#define SLV_PAN_H

#include <math.h>      /* sin, cos */

/* pi/2. M_PI is not in ISO C (only a common extension), so the constant is spelt
 * out here to keep the header dependency-free and portable. */
#ifndef SLV_PAN_HALF_PI
#define SLV_PAN_HALF_PI 1.57079632679489661923
#endif

/*! \brief Constant-power stereo pan gains for a source at horizontal \p azimuth
 * (radians; 0 = ahead, positive = listener's RIGHT, per Amendment 4). Writes the
 * left gain to \p outL and the right gain to \p outR (either may be NULL). The
 * gains satisfy *outL^2 + *outR^2 == 1 for every azimuth (Amendment 5); ahead and
 * behind both render centred. Never NaN: sin/cos are total over the reals. */
static inline void slv_pan(double azimuth, float *outL, float *outR) {
	/* sin(azimuth) is the pan signal (Amendment 5): continuous, no clamp needed. */
	double p = (sin(azimuth) + 1.0) / 2.0;   /* 0 = hard left ... 1 = hard right */
	double a = p * SLV_PAN_HALF_PI;
	if(outL)
		*outL = (float)cos(a);
	if(outR)
		*outR = (float)sin(a);
}

#endif /* SLV_PAN_H */
