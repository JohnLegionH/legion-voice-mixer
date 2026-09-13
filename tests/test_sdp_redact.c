/* Unit tests for src/sdp_redact.h (O-66). libc only. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../src/sdp_redact.h"

static int failures = 0, checks = 0;

static void expect_str(const char *name, const char *in, const char *want) {
	checks++;
	char *got = slv_sdp_redact(in);
	if(got == NULL || strcmp(got, want) != 0) {
		failures++;
		printf("FAIL %s\n  want: [%s]\n  got:  [%s]\n", name, want, got ? got : "(null)");
	}
	free(got);
}

static void expect_absent(const char *name, const char *in, const char *needle) {
	checks++;
	char *got = slv_sdp_redact(in);
	if(got == NULL || strstr(got, needle) != NULL) {
		failures++;
		printf("FAIL %s: '%s' still present\n", name, needle);
	}
	free(got);
}

int main(void) {
	expect_str("ufrag crlf", "a=ice-ufrag:abcd\r\n", "a=ice-ufrag:<redacted>\r\n");
	expect_str("pwd lf", "a=ice-pwd:0123456789abcdefghijkl\n", "a=ice-pwd:<redacted>\n");
	expect_str("fingerprint keeps hash name",
		"a=fingerprint:sha-256 AB:CD:EF:01\r\n", "a=fingerprint:sha-256 <redacted>\r\n");
	expect_str("fingerprint without hash token", "a=fingerprint:ABCDEF\r\n", "a=fingerprint:<redacted>\r\n");
	expect_str("no trailing newline", "a=ice-pwd:secret", "a=ice-pwd:<redacted>");
	expect_str("short value grows", "a=ice-ufrag:x\r\n", "a=ice-ufrag:<redacted>\r\n");
	expect_str("empty", "", "");
	expect_str("untouched lines",
		"v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=rtpmap:111 opus/48000/2\r\na=ptime:20\r\n",
		"v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=rtpmap:111 opus/48000/2\r\na=ptime:20\r\n");
	/* prefix match only at line start */
	expect_str("mid-line mention kept", "s=a=ice-pwd:not-an-attr\r\n", "s=a=ice-pwd:not-an-attr\r\n");
	expect_str("similar attribute kept", "a=ice-options:trickle\r\n", "a=ice-options:trickle\r\n");

	const char *offer =
		"v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\n"
		"a=group:BUNDLE 0 1\r\n"
		"m=audio 9 UDP/TLS/RTP/SAVPF 111\r\nc=IN IP4 0.0.0.0\r\n"
		"a=ice-ufrag:Uf1x\r\na=ice-pwd:PwdSecretValue1234567890\r\n"
		"a=fingerprint:sha-256 11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00\r\n"
		"a=setup:actpass\r\na=mid:0\r\na=rtpmap:111 opus/48000/2\r\n"
		"m=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\n"
		"a=ice-ufrag:Uf2y\r\na=ice-pwd:OtherSecret0987654321\r\n"
		"a=fingerprint:sha-256 AA:BB:CC\r\na=mid:1\r\n";
	expect_absent("full offer ufrag 1", offer, "Uf1x");
	expect_absent("full offer pwd 1", offer, "PwdSecretValue");
	expect_absent("full offer fingerprint 1", offer, "11:22:33");
	expect_absent("full offer ufrag 2", offer, "Uf2y");
	expect_absent("full offer pwd 2", offer, "OtherSecret");
	expect_absent("full offer fingerprint 2", offer, "AA:BB:CC");
	{
		checks++;
		char *got = slv_sdp_redact(offer);
		if(got == NULL || strstr(got, "a=rtpmap:111 opus/48000/2\r\nm=application") == NULL
				|| strstr(got, "a=setup:actpass\r\na=mid:0\r\n") == NULL) {
			failures++;
			printf("FAIL full offer: non-credential lines altered\n");
		}
		free(got);
	}
	checks++;
	if(slv_sdp_redact(NULL) != NULL) {
		failures++;
		printf("FAIL NULL input\n");
	}

	printf("test_sdp_redact: %d/%d passed\n", checks - failures, checks);
	return failures == 0 ? 0 : 1;
}
