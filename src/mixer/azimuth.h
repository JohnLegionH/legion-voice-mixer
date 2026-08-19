/*! \file    azimuth.h
 * \author   Legion Voice Mixer project
 * \copyright GNU General Public License v3
 * \brief    Horizontal azimuth of a source in a listener's head frame (Phase 3b item 4)
 *
 * \details  Pure math for amplitude panning: given the listener position \p lp,
 * the listener/camera orientation \p lh, and a source position \p sp (all in the
 * region frame; azimuth is scale- and origin-invariant, so the ×100 storage unit
 * is immaterial here), returns the horizontal azimuth to the source in the
 * listener's head frame:
 *   0 = directly ahead, positive = to the listener's RIGHT.
 * Sign per docs/voice/phase3b-design-brief.md Amendment 4 (a source to the right
 * is louder in the right channel), so a positive azimuth pans right.
 *
 * Coordinate convention (SL/OpenSim, Amendment 4): X east, Y north, Z up,
 * right-handed; an avatar's forward is local +X rotated by its orientation.
 * Azimuth is HORIZONTAL — computed from the region-XY projection of the forward
 * vector and the source direction — so it depends on the listener's heading (yaw)
 * only, not pitch or roll.
 *
 * \p lh arrives ×100 and int-truncated over the wire, so it is NOT a unit
 * quaternion (Amendment 4). It is normalized here before use; a zero-magnitude
 * quaternion falls back to identity (forward = +X). Header-only \c static
 * \c inline, dependency-free beyond libm, matching \ref mixer/vec3.h — no
 * translation unit, unit-tested via \c #include (tests/test_azimuth.c).
 */

#ifndef SLV_AZIMUTH_H
#define SLV_AZIMUTH_H

#include <math.h>      /* sqrt, atan2 */
#include "vec3.h"      /* slv_vec3 / slv_quat (via ../sldata.h), slv_vec3_sub */

/*! \brief Unit-normalize \p q; a zero-magnitude quaternion returns identity
 * {0,0,0,1}. lh/sh arrive ×100-truncated (non-unit), so callers must normalize
 * before rotating (Amendment 4). */
static inline slv_quat slv_quat_normalize(slv_quat q) {
	double m = sqrt(q.x * q.x + q.y * q.y + q.z * q.z + q.w * q.w);
	if(m <= 0.0) {
		slv_quat id = { 0.0, 0.0, 0.0, 1.0 };
		return id;
	}
	slv_quat r = { q.x / m, q.y / m, q.z / m, q.w / m };
	return r;
}

/*! \brief Horizontal azimuth (radians) of source \p sp for a listener at \p lp
 * oriented \p lh. 0 = directly ahead; positive = to the listener's RIGHT
 * (Amendment 4). Pitch/roll-independent (uses the region-XY projection). A source
 * directly overhead, or a listener facing straight up/down, yields 0 (defined,
 * never NaN — atan2(0,0) == 0). */
static inline double slv_azimuth(slv_vec3 lp, slv_quat lh, slv_vec3 sp) {
	slv_quat q = slv_quat_normalize(lh);
	/* Listener forward in the region frame = q * (1,0,0): the first column of the
	 * quaternion rotation matrix. Only the horizontal (x,y) part is needed. */
	double fx = 1.0 - 2.0 * (q.y * q.y + q.z * q.z);
	double fy = 2.0 * (q.x * q.y + q.w * q.z);
	slv_vec3 d = slv_vec3_sub(sp, lp);
	/* Signed horizontal angle from forward to the source direction. cross > 0 means
	 * the source is counter-clockwise (to the LEFT) of forward; Amendment 4 wants
	 * positive = RIGHT, so negate. atan2(0,0) == 0 keeps overhead / straight-up
	 * cases defined. */
	double cross = fx * d.y - fy * d.x;
	double dot   = fx * d.x + fy * d.y;
	return -atan2(cross, dot);
}

#endif /* SLV_AZIMUTH_H */
