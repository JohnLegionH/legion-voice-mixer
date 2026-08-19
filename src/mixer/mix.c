/*! \file    mix.c
 * \author   Legion Voice Mixer project
 * \copyright GNU General Public License v3
 * \brief    Implementation of the pure N-minus-one mixing math (see mix.h).
 *
 * \details  libc + libm only, so this translation unit compiles unchanged into
 * both the plugin .so and the standalone unit-test binary (tests/test_mix.c).
 */

#include "mix.h"

#include <math.h>

int slv_mix_nminus1_stereo(float *out, size_t n,
                           const float *const *sources, const int *active,
                           const int *mutes,
                           const float *gainsL, const float *gainsR,
                           int nsrc, int self) {
	if(out == NULL || n == 0)
		return 0;
	for(size_t i = 0; i < n; i++)
		out[i] = 0.0f;
	int summed = 0;
	for(int s = 0; s < nsrc; s++) {
		if(s == self)
			continue;
		if(active == NULL || !active[s])
			continue;
		if(mutes != NULL && mutes[s])
			continue;
		const float *src = sources ? sources[s] : NULL;
		if(src == NULL)
			continue;
		float gL = (gainsL != NULL) ? gainsL[s] : 1.0f;
		float gR = (gainsR != NULL) ? gainsR[s] : 1.0f;
		/* Fully silent in both channels drops the source (matches the scalar
		 * path's g==0 skip, so the wrapper below counts identically). */
		if(gL == 0.0f && gR == 0.0f)
			continue;
		/* Interleaved stereo: even index = left, odd = right. */
		size_t i = 0;
		for(; i + 1 < n; i += 2) {
			out[i]     += src[i]     * gL;
			out[i + 1] += src[i + 1] * gR;
		}
		/* Odd tail (a non-stereo n, exercised only by the scalar wrapper where
		 * gL==gR): apply the left gain so the wrapper stays sample-for-sample
		 * identical to the old scalar mix for every n. */
		if(i < n)
			out[i] += src[i] * gL;
		summed++;
	}
	return summed;
}

int slv_mix_nminus1(float *out, size_t n,
                    const float *const *sources, const int *active,
                    const int *mutes, const float *gains,
                    int nsrc, int self) {
	/* Thin wrapper (Amendment 5): the mono N-minus-one mix is the stereo mix with
	 * left gain == right gain == the scalar gain. Multiplying by an exact 1.0f is
	 * identity in IEEE float, so this reproduces the old unity fast path bit for
	 * bit; the 26 existing test_mix checks pass unchanged. */
	return slv_mix_nminus1_stereo(out, n, sources, active, mutes,
	                              gains, gains, nsrc, self);
}

void slv_mix_apply_gain(float *buf, size_t n, float gain) {
	if(buf == NULL || gain == 1.0f)
		return;
	for(size_t i = 0; i < n; i++)
		buf[i] *= gain;
}

void slv_mix_clamp(float *buf, size_t n) {
	if(buf == NULL)
		return;
	for(size_t i = 0; i < n; i++) {
		if(buf[i] > 1.0f)
			buf[i] = 1.0f;
		else if(buf[i] < -1.0f)
			buf[i] = -1.0f;
	}
}

double slv_mix_rms(const float *buf, size_t n) {
	if(buf == NULL || n == 0)
		return 0.0;
	double sumsq = 0.0;
	for(size_t i = 0; i < n; i++) {
		double v = (double)buf[i];
		sumsq += v * v;
	}
	return sqrt(sumsq / (double)n);
}

int slv_mix_is_silent(const float *buf, size_t n, double threshold) {
	return slv_mix_rms(buf, n) <= threshold;
}
