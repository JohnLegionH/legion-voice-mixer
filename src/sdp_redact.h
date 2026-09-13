/* O-66: redact ICE credentials and the DTLS fingerprint from an SDP before it is logged.
 *
 * The values of a=ice-ufrag:, a=ice-pwd: and a=fingerprint: lines are replaced by
 * "<redacted>" (a fingerprint keeps its hash name, e.g. "a=fingerprint:sha-256 <redacted>").
 * Every other byte is copied unchanged, CRLF or LF line endings alike.
 *
 * Header-only, libc only, so tests/test_sdp_redact.c exercises it without Janus.
 * (The Janus core already anonymises the offer before a plugin sees it; this is the
 * guard against a future path that logs an un-anonymised SDP.) */
#ifndef SLV_SDP_REDACT_H
#define SLV_SDP_REDACT_H

#include <stdlib.h>
#include <string.h>

#define SLV_SDP_REDACTED "<redacted>"

/* Writes the redacted copy into out (when non-NULL) and returns its length, excluding NUL. */
static inline size_t slv_sdp_redact_into(const char *sdp, char *out) {
	static const char *const prefixes[] = { "a=ice-ufrag:", "a=ice-pwd:", "a=fingerprint:" };
	size_t n = 0;
	const char *p = sdp;
	while(*p != '\0') {
		/* p is at the start of a line */
		size_t keep = 0;
		for(size_t i = 0; i < sizeof(prefixes) / sizeof(prefixes[0]); i++) {
			size_t len = strlen(prefixes[i]);
			if(strncmp(p, prefixes[i], len) == 0) {
				keep = len;
				if(i == 2) {
					/* keep the hash function token ("sha-256 ") */
					const char *q = p + len;
					while(*q != '\0' && *q != ' ' && *q != '\r' && *q != '\n')
						q++;
					if(*q == ' ')
						keep = (size_t)(q - p) + 1;
				}
				break;
			}
		}
		const char *eol = p;
		while(*eol != '\0' && *eol != '\r' && *eol != '\n')
			eol++;
		if(keep > 0) {
			if(out) {
				memcpy(out + n, p, keep);
				memcpy(out + n + keep, SLV_SDP_REDACTED, sizeof(SLV_SDP_REDACTED) - 1);
			}
			n += keep + sizeof(SLV_SDP_REDACTED) - 1;
		} else {
			if(out)
				memcpy(out + n, p, (size_t)(eol - p));
			n += (size_t)(eol - p);
		}
		p = eol;
		while(*p == '\r' || *p == '\n') {
			if(out)
				out[n] = *p;
			n++;
			p++;
		}
	}
	if(out)
		out[n] = '\0';
	return n;
}

/* Returns a malloc'd redacted copy (caller free()s), or NULL on NULL input / OOM. */
static inline char *slv_sdp_redact(const char *sdp) {
	if(sdp == NULL)
		return NULL;
	char *out = malloc(slv_sdp_redact_into(sdp, NULL) + 1);
	if(out != NULL)
		slv_sdp_redact_into(sdp, out);
	return out;
}

#endif
