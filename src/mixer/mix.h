/*! \file    mix.h
 * \author   Legion Voice Mixer project
 * \copyright GNU General Public License v3
 * \brief    Pure mixing math for the per-room N-minus-one conference mix (Phase 2)
 *
 * \details  These are the arithmetic primitives the per-region tick uses to
 * build each listener's mix. They are deliberately DEPENDENCY-FREE beyond libc
 * + libm: no Janus, no Opus, no glib. That is what lets the mixing math be
 * unit-tested as a plain C binary (tests/test_mix.c, `make test`) with no
 * gateway running, and it keeps the hot loop trivially auditable.
 *
 * All buffers are interleaved float PCM in the nominal [-1,1] range; `n` is the
 * TOTAL sample count (per-channel samples * channels), so these functions are
 * channel-count agnostic (stereo is just n = samples*2).
 */

#ifndef SLV_MIX_H
#define SLV_MIX_H

#include <stddef.h>

/*! \brief Sum the active sources, EXCLUDING one (the listener's own source),
 * into \p out — the N-minus-one conference mix.
 *
 * \p out is fully overwritten (zeroed then summed); it need not be pre-cleared.
 * For each source i in [0,nsrc): it contributes only when \p active[i] is
 * non-zero, i != \p self, and \p mutes (if non-NULL) has mutes[i]==0. Its
 * samples are scaled by \p gains[i] when \p gains is non-NULL (unity otherwise).
 *
 * \param out     destination, length \p n (interleaved float)
 * \param n       total samples (per-channel samples * channels)
 * \param sources array of \p nsrc pointers to source buffers, each length \p n
 *                (a NULL entry is treated as silent/absent)
 * \param active  array of \p nsrc ints; non-zero = source currently active
 * \param mutes   array of \p nsrc ints or NULL; non-zero = this listener has
 *                muted source i (per-source mute, excluded from the sum)
 * \param gains   array of \p nsrc floats or NULL; per-source linear gain
 * \param nsrc    number of sources
 * \param self    index to exclude (the listener's own source), or -1 for none
 * \returns the number of sources actually summed (0 == silent mix).
 */
int slv_mix_nminus1(float *out, size_t n,
                    const float *const *sources, const int *active,
                    const int *mutes, const float *gains,
                    int nsrc, int self);

/*! \brief Per-channel (stereo) N-minus-one mix: like \ref slv_mix_nminus1 but with
 * separate left and right gains per source, for amplitude panning (Phase 3b item 4).
 *
 * \p out is interleaved stereo (even index = left, odd = right) and is fully
 * overwritten. A source contributes when \p active[i] is non-zero, i != \p self,
 * and \p mutes (if non-NULL) has mutes[i]==0; its even samples are scaled by
 * \p gainsL[i] and its odd samples by \p gainsR[i] (unity when the array is NULL).
 * A source that is silent in BOTH channels (gainsL[i]==gainsR[i]==0) is dropped and
 * not counted, matching \ref slv_mix_nminus1's zero-gain skip.
 *
 * \ref slv_mix_nminus1 is a thin wrapper over this with gainsL == gainsR == gains
 * (Amendment 5): the pan gains are computed per source from the azimuth
 * (\ref mixer/pan.h) and cannot be folded into the shared, tick-owned source
 * buffers, so panning must happen here at mix time.
 *
 * \param out     destination, length \p n (interleaved stereo float)
 * \param n       total samples (per-channel samples * 2)
 * \param sources array of \p nsrc pointers to source buffers, each length \p n
 * \param active  array of \p nsrc ints; non-zero = source currently active
 * \param mutes   array of \p nsrc ints or NULL; non-zero = this listener muted i
 * \param gainsL  array of \p nsrc floats or NULL; per-source LEFT linear gain
 * \param gainsR  array of \p nsrc floats or NULL; per-source RIGHT linear gain
 * \param nsrc    number of sources
 * \param self    index to exclude (the listener's own source), or -1 for none
 * \returns the number of sources actually summed (0 == silent mix).
 */
int slv_mix_nminus1_stereo(float *out, size_t n,
                           const float *const *sources, const int *active,
                           const int *mutes,
                           const float *gainsL, const float *gainsR,
                           int nsrc, int self);

/*! \brief Multiply \p buf in place by a scalar linear \p gain. */
void slv_mix_apply_gain(float *buf, size_t n, float gain);

/*! \brief Clamp every sample of \p buf to [-1,1] in place. Guards the Opus
 * float encoder from a summed mix that overshoots unity when several talkers
 * are loud at once. */
void slv_mix_clamp(float *buf, size_t n);

/*! \brief RMS (root-mean-square) energy of \p buf, in the same [0,~1] scale as
 * the samples. Returns 0 for an empty buffer. Used both for the per-participant
 * power/VAD report and for the encode-skip silence test. */
double slv_mix_rms(const float *buf, size_t n);

/*! \brief Silence test for DTX / encode-skip: non-zero when the buffer's RMS is
 * at or below \p threshold (i.e. nothing audible to encode). */
int slv_mix_is_silent(const float *buf, size_t n, double threshold);

#endif /* SLV_MIX_H */
