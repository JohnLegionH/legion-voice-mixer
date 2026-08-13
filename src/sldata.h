/*! \file    sldata.h
 * \author   Legion Voice Mixer project
 * \copyright GNU General Public License v3
 * \brief    Parser for the Second Life WebRTC voice data-channel payload ("SLData")
 *
 * \details  The SL WebRTC voice client sends small JSON objects on the peer's
 * data channel carrying spatial/presence state. This module parses one such
 * object into a flat, typed \ref slv_sldata struct.
 *
 * It is deliberately DEPENDENCY-FREE beyond jansson and libc: it does NOT
 * include any Janus headers, so it compiles both into the plugin .so and into
 * a standalone unit-test binary (see tests/test_sldata.c, `make test`).
 *
 * Field set (docs/voice/webrtc-voice-spec.md §9 client->mixer field list;
 * `lh` semantics confirmed by §6):
 *   j, l, sp, sh, lp, lh, m, ug
 * plus the slvoice `echo` extension (spec §4.2 echo-test control; see
 * docs/sldata-extensions.md).
 *
 * Phase-1 policy: we PARSE and STORE the latest values; we do not act on the
 * geometry (no spatial logic until Phase 2). Unknown fields are ignored
 * silently; malformed JSON is reported (never crashes) so the caller can log
 * it at warn. The parse is tolerant of both array and object vector encodings.
 */

#ifndef SLV_SLDATA_H
#define SLV_SLDATA_H

#include <stddef.h>

/*! \brief Hard cap on a single SLData payload. Anything larger is rejected
 * without parsing (defends the JSON parser from oversized input on the data
 * channel). SL position updates are a few hundred bytes at most. */
#define SLV_SLDATA_MAX_BYTES 8192

/*! \brief Which recognised fields were present in a parsed payload.
 * Stored as a bitmask so diagnostics can report exactly what the last message
 * carried without keeping the raw JSON. */
typedef enum slv_field {
	SLV_FIELD_J    = 1u << 0,  /*!< "j"  — join/roster marker (presence only in Phase 1) */
	SLV_FIELD_L    = 1u << 1,  /*!< "l"  — leave marker (presence only in Phase 1) */
	SLV_FIELD_SP   = 1u << 2,  /*!< "sp" — self position (x,y,z) */
	SLV_FIELD_SH   = 1u << 3,  /*!< "sh" — self heading/orientation (x,y,z,w) */
	SLV_FIELD_LP   = 1u << 4,  /*!< "lp" — listener position (x,y,z) */
	SLV_FIELD_LH   = 1u << 5,  /*!< "lh" — listener heading/orientation (x,y,z,w) */
	SLV_FIELD_M    = 1u << 6,  /*!< "m"  — mute flag */
	SLV_FIELD_UG   = 1u << 7,  /*!< "ug" — user gain */
	SLV_FIELD_ECHO = 1u << 8,  /*!< "echo" — slvoice echo-mode toggle (extension) */
} slv_field;

/*! \brief A 3D vector in region-local metres. */
typedef struct slv_vec3 { double x, y, z; } slv_vec3;

/*! \brief An orientation quaternion. */
typedef struct slv_quat { double x, y, z, w; } slv_quat;

/*! \brief Parsed SLData. Only members whose bit is set in \c fields_seen carry
 * meaningful values; the rest are zero-initialised. */
typedef struct slv_sldata {
	unsigned fields_seen;   /*!< bitmask of ::slv_field that were present and well-typed */
	slv_vec3 sp;            /*!< self position, if SLV_FIELD_SP */
	slv_quat sh;            /*!< self heading, if SLV_FIELD_SH */
	slv_vec3 lp;            /*!< listener position, if SLV_FIELD_LP */
	slv_quat lh;            /*!< listener heading, if SLV_FIELD_LH */
	double   ug;            /*!< user gain, if SLV_FIELD_UG */
	int      m;             /*!< mute (0/1), if SLV_FIELD_M */
	int      echo;          /*!< echo on(1)/off(0), if SLV_FIELD_ECHO */
} slv_sldata;

/*! \brief Result of a parse attempt. */
typedef enum slv_sldata_status {
	SLV_SLDATA_OK = 0,     /*!< valid JSON object; >=1 recognised field parsed (see fields_seen) */
	SLV_SLDATA_EMPTY,      /*!< valid JSON object but no recognised fields */
	SLV_SLDATA_MALFORMED,  /*!< not valid JSON, or not a JSON object */
	SLV_SLDATA_TOOBIG,     /*!< payload exceeded ::SLV_SLDATA_MAX_BYTES; not parsed */
} slv_sldata_status;

/*! \brief Parse one SLData payload.
 * \param buf   pointer to the raw payload (NOT required to be NUL-terminated)
 * \param len   payload length in bytes
 * \param out   destination; always fully zero-initialised by this call first
 * \returns a ::slv_sldata_status. On OK/EMPTY, \c out->fields_seen tells the
 *          caller exactly which fields were populated. Never dereferences past
 *          \c len; never aborts on bad input. */
slv_sldata_status slv_sldata_parse(const char *buf, size_t len, slv_sldata *out);

/*! \brief Render a fields_seen bitmask as a stable, comma-separated key list
 * (e.g. "sp,sh,echo") for diagnostics. Always NUL-terminates.
 * \returns \c dst. */
const char *slv_sldata_fields_str(unsigned fields_seen, char *dst, size_t dstlen);

#endif /* SLV_SLDATA_H */
