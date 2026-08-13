/*! \file    sldata.c
 * \author   Legion Voice Mixer project
 * \copyright GNU General Public License v3
 * \brief    Implementation of the SL WebRTC voice data-channel parser.
 *
 * \details  jansson + libc only (no Janus headers), so this translation unit is
 * shared verbatim between the plugin .so and the unit-test binary. See
 * sldata.h for the contract and docs/sldata-extensions.md for field semantics.
 */

#include "sldata.h"

#include <string.h>
#include <jansson.h>

/* Read a number (json integer or real) into *dst. Returns 1 on success. */
static int slv_get_number(json_t *v, double *dst) {
	if(v == NULL)
		return 0;
	if(json_is_real(v)) {
		*dst = json_real_value(v);
		return 1;
	}
	if(json_is_integer(v)) {
		*dst = (double)json_integer_value(v);
		return 1;
	}
	return 0;
}

/* Read a boolean-ish value (true/false or integer 0/non-0) into *dst (0/1).
 * Returns 1 on success. */
static int slv_get_bool(json_t *v, int *dst) {
	if(v == NULL)
		return 0;
	if(json_is_boolean(v)) {
		*dst = json_is_true(v) ? 1 : 0;
		return 1;
	}
	if(json_is_integer(v)) {
		*dst = json_integer_value(v) != 0 ? 1 : 0;
		return 1;
	}
	return 0;
}

/* Read an n-component vector that may be encoded either as a JSON array
 * [c0, c1, ...] or as an object with the given component key names. `keys` is
 * an array of `n` C-string names (used only for the object form). Writes up to
 * n doubles into out[]. Returns 1 only if ALL n components were present and
 * numeric; otherwise 0 and out[] is left untouched. Tolerant of extra
 * array/object members (ignored). */
static int slv_get_vecN(json_t *v, int n, const char *const *keys, double *out) {
	if(v == NULL)
		return 0;
	double tmp[4];
	if(n > 4)
		n = 4;
	if(json_is_array(v)) {
		if((int)json_array_size(v) < n)
			return 0;
		for(int i = 0; i < n; i++) {
			if(!slv_get_number(json_array_get(v, i), &tmp[i]))
				return 0;
		}
	} else if(json_is_object(v)) {
		for(int i = 0; i < n; i++) {
			if(!slv_get_number(json_object_get(v, keys[i]), &tmp[i]))
				return 0;
		}
	} else {
		return 0;
	}
	for(int i = 0; i < n; i++)
		out[i] = tmp[i];
	return 1;
}

slv_sldata_status slv_sldata_parse(const char *buf, size_t len, slv_sldata *out) {
	/* Always start from a clean slate so partial parses never leak stale data. */
	if(out != NULL)
		memset(out, 0, sizeof(*out));
	if(buf == NULL || len == 0 || out == NULL)
		return SLV_SLDATA_MALFORMED;
	if(len > SLV_SLDATA_MAX_BYTES)
		return SLV_SLDATA_TOOBIG;

	json_error_t err;
	/* json_loadb takes an explicit length: the data-channel buffer is NOT
	 * guaranteed to be NUL-terminated, so we must not rely on strlen. */
	json_t *root = json_loadb(buf, len, 0, &err);
	if(root == NULL)
		return SLV_SLDATA_MALFORMED;
	if(!json_is_object(root)) {
		json_decref(root);
		return SLV_SLDATA_MALFORMED;
	}

	static const char *const xyz[3]  = { "x", "y", "z" };
	static const char *const xyzw[4] = { "x", "y", "z", "w" };
	double vec[4];

	/* Presence markers: j / l. We record only that they appeared; their inner
	 * shape (roster maps, etc.) is not decoded in Phase 1. */
	if(json_object_get(root, "j") != NULL)
		out->fields_seen |= SLV_FIELD_J;
	if(json_object_get(root, "l") != NULL)
		out->fields_seen |= SLV_FIELD_L;

	/* Positions / orientations. Wrong-typed values are ignored (not fatal). */
	if(slv_get_vecN(json_object_get(root, "sp"), 3, xyz, vec)) {
		out->sp.x = vec[0]; out->sp.y = vec[1]; out->sp.z = vec[2];
		out->fields_seen |= SLV_FIELD_SP;
	}
	if(slv_get_vecN(json_object_get(root, "sh"), 4, xyzw, vec)) {
		out->sh.x = vec[0]; out->sh.y = vec[1]; out->sh.z = vec[2]; out->sh.w = vec[3];
		out->fields_seen |= SLV_FIELD_SH;
	}
	if(slv_get_vecN(json_object_get(root, "lp"), 3, xyz, vec)) {
		out->lp.x = vec[0]; out->lp.y = vec[1]; out->lp.z = vec[2];
		out->fields_seen |= SLV_FIELD_LP;
	}
	if(slv_get_vecN(json_object_get(root, "lh"), 4, xyzw, vec)) {
		out->lh.x = vec[0]; out->lh.y = vec[1]; out->lh.z = vec[2]; out->lh.w = vec[3];
		out->fields_seen |= SLV_FIELD_LH;
	}

	/* Scalars: m (mute), ug (user gain). */
	{
		int b;
		if(slv_get_bool(json_object_get(root, "m"), &b)) {
			out->m = b;
			out->fields_seen |= SLV_FIELD_M;
		}
	}
	{
		double g;
		if(slv_get_number(json_object_get(root, "ug"), &g)) {
			out->ug = g;
			out->fields_seen |= SLV_FIELD_UG;
		}
	}

	/* slvoice echo extension. */
	{
		int b;
		if(slv_get_bool(json_object_get(root, "echo"), &b)) {
			out->echo = b;
			out->fields_seen |= SLV_FIELD_ECHO;
		}
	}

	json_decref(root);
	return out->fields_seen ? SLV_SLDATA_OK : SLV_SLDATA_EMPTY;
}

const char *slv_sldata_fields_str(unsigned fields_seen, char *dst, size_t dstlen) {
	if(dst == NULL || dstlen == 0)
		return dst;
	dst[0] = '\0';
	static const struct { unsigned bit; const char *name; } names[] = {
		{ SLV_FIELD_J, "j" }, { SLV_FIELD_L, "l" },
		{ SLV_FIELD_SP, "sp" }, { SLV_FIELD_SH, "sh" },
		{ SLV_FIELD_LP, "lp" }, { SLV_FIELD_LH, "lh" },
		{ SLV_FIELD_M, "m" }, { SLV_FIELD_UG, "ug" },
		{ SLV_FIELD_ECHO, "echo" },
	};
	size_t used = 0;
	for(size_t i = 0; i < sizeof(names)/sizeof(names[0]); i++) {
		if(!(fields_seen & names[i].bit))
			continue;
		const char *n = names[i].name;
		size_t nlen = strlen(n);
		/* need: optional comma + name + NUL */
		size_t need = (used ? 1 : 0) + nlen + 1;
		if(used + need > dstlen)
			break;
		if(used)
			dst[used++] = ',';
		memcpy(dst + used, n, nlen);
		used += nlen;
		dst[used] = '\0';
	}
	return dst;
}
