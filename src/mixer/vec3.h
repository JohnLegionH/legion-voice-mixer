/*! \file    vec3.h
 * \author   Legion Voice Mixer project
 * \copyright GNU General Public License v3
 * \brief    Pure 3D vector math for the spatial mixer (Phase 3b)
 *
 * \details  The arithmetic primitives the geometry snapshot (the §7.1 camera
 * leash) and, later, the distance / attenuation / panning DSP build on. Like
 * \ref mixer/mix.h these are DEPENDENCY-FREE beyond libm and header-only
 * (\c static \c inline), so they add no translation unit / Makefile entry and
 * can be unit-tested by \c #include alone. They operate on \ref slv_vec3
 * (region-local coordinates); the routines are unit-agnostic — the caller keeps
 * the scale consistent (geometry is stored ×100, see
 * docs/voice/phase3b-design-brief.md "Resolved: the geometry unit").
 */

#ifndef SLV_VEC3_H
#define SLV_VEC3_H

#include <math.h>       /* sqrt */
#include "../sldata.h"  /* slv_vec3 */

/*! \brief \p a − \p b, componentwise. */
static inline slv_vec3 slv_vec3_sub(slv_vec3 a, slv_vec3 b) {
	slv_vec3 r = { a.x - b.x, a.y - b.y, a.z - b.z };
	return r;
}

/*! \brief Euclidean length |\p v|. */
static inline double slv_vec3_mag(slv_vec3 v) {
	return sqrt(v.x * v.x + v.y * v.y + v.z * v.z);
}

/*! \brief \p a + \p s·\p b, componentwise (scaled-add). Used for the leash
 * projection: sp + (radius / dist)·(lp − sp). */
static inline slv_vec3 slv_vec3_madd(slv_vec3 a, double s, slv_vec3 b) {
	slv_vec3 r = { a.x + s * b.x, a.y + s * b.y, a.z + s * b.z };
	return r;
}

#endif /* SLV_VEC3_H */
