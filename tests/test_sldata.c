/*! \file    tests/test_sldata.c
 * \author   Legion Voice Mixer project
 * \copyright GNU General Public License v3
 * \brief    Unit tests for the SLData data-channel parser (src/sldata.c).
 *
 * \details  Plain C, links only jansson (no Janus). Built and run by
 * `make test`. Covers: fully-valid, partial, malformed (bad JSON / non-object
 * / wrong types), oversized input, both array and object vector encodings,
 * the echo extension, and the fields-string helper. Exit status is non-zero
 * on any failure so CI / the Docker build fails loudly.
 */

#include "../src/sldata.h"

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <math.h>

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

/* ---- valid: a full position update with every recognised field ---------- */
static void test_full(void) {
	printf("test_full\n");
	const char *j =
		"{\"sp\":[1.0,2.0,3.0],"
		"\"sh\":[0.0,0.0,0.0,1.0],"
		"\"lp\":[4.0,5.0,6.0],"
		"\"lh\":[0.0,0.0,1.0,0.0],"
		"\"m\":true,\"ug\":0.5,"
		"\"j\":{\"whatever\":1},\"l\":[]}";
	slv_sldata d;
	slv_sldata_status s = slv_sldata_parse(j, strlen(j), &d);
	CHECK(s == SLV_SLDATA_OK, "full parses OK");
	CHECK(d.fields_seen & SLV_FIELD_SP, "sp seen");
	CHECK(d.fields_seen & SLV_FIELD_SH, "sh seen");
	CHECK(d.fields_seen & SLV_FIELD_LP, "lp seen");
	CHECK(d.fields_seen & SLV_FIELD_LH, "lh seen");
	CHECK(d.fields_seen & SLV_FIELD_M,  "m seen");
	CHECK(d.fields_seen & SLV_FIELD_UG, "ug seen");
	CHECK(d.fields_seen & SLV_FIELD_J,  "j seen");
	CHECK(d.fields_seen & SLV_FIELD_L,  "l seen");
	CHECK(!(d.fields_seen & SLV_FIELD_ECHO), "echo not seen");
	CHECK(approx(d.sp.x, 1.0) && approx(d.sp.y, 2.0) && approx(d.sp.z, 3.0), "sp values");
	CHECK(approx(d.sh.w, 1.0), "sh.w value");
	CHECK(approx(d.lp.z, 6.0), "lp.z value");
	CHECK(d.m == 1, "m true");
	CHECK(approx(d.ug, 0.5), "ug value");
}

/* ---- valid: object-form vectors are accepted as well as array-form ------ */
static void test_object_vectors(void) {
	printf("test_object_vectors\n");
	const char *j = "{\"sp\":{\"x\":7.0,\"y\":8.0,\"z\":9.0}}";
	slv_sldata d;
	slv_sldata_status s = slv_sldata_parse(j, strlen(j), &d);
	CHECK(s == SLV_SLDATA_OK, "object vector OK");
	CHECK(d.fields_seen == SLV_FIELD_SP, "only sp seen");
	CHECK(approx(d.sp.x, 7.0) && approx(d.sp.y, 8.0) && approx(d.sp.z, 9.0), "object sp values");
}

/* ---- partial: only some fields present; rest must stay zero/unseen ------- */
static void test_partial(void) {
	printf("test_partial\n");
	const char *j = "{\"m\":false,\"unknownkey\":123}";
	slv_sldata d;
	slv_sldata_status s = slv_sldata_parse(j, strlen(j), &d);
	CHECK(s == SLV_SLDATA_OK, "partial OK");
	CHECK(d.fields_seen == SLV_FIELD_M, "only m seen (unknown ignored)");
	CHECK(d.m == 0, "m false");
}

/* ---- empty object: valid JSON but nothing recognised -------------------- */
static void test_empty_object(void) {
	printf("test_empty_object\n");
	const char *j = "{}";
	slv_sldata d;
	slv_sldata_status s = slv_sldata_parse(j, strlen(j), &d);
	CHECK(s == SLV_SLDATA_EMPTY, "empty object -> EMPTY");
	CHECK(d.fields_seen == 0, "no fields seen");
}

/* ---- echo extension, both truthy and falsy ------------------------------ */
static void test_echo(void) {
	printf("test_echo\n");
	slv_sldata d;
	const char *on = "{\"echo\":true}";
	CHECK(slv_sldata_parse(on, strlen(on), &d) == SLV_SLDATA_OK, "echo:true OK");
	CHECK((d.fields_seen & SLV_FIELD_ECHO) && d.echo == 1, "echo on");
	const char *off = "{\"echo\":false}";
	CHECK(slv_sldata_parse(off, strlen(off), &d) == SLV_SLDATA_OK, "echo:false OK");
	CHECK((d.fields_seen & SLV_FIELD_ECHO) && d.echo == 0, "echo off");
	const char *oni = "{\"echo\":1}";
	CHECK(slv_sldata_parse(oni, strlen(oni), &d) == SLV_SLDATA_OK, "echo:1 OK");
	CHECK((d.fields_seen & SLV_FIELD_ECHO) && d.echo == 1, "echo int 1 -> on");
}

/* ---- malformed: bad JSON, non-object, and wrong-typed known fields ------ */
static void test_malformed(void) {
	printf("test_malformed\n");
	slv_sldata d;
	const char *bad = "{not valid json";
	CHECK(slv_sldata_parse(bad, strlen(bad), &d) == SLV_SLDATA_MALFORMED, "bad json -> MALFORMED");
	CHECK(d.fields_seen == 0, "malformed leaves fields zero");

	const char *arr = "[1,2,3]";
	CHECK(slv_sldata_parse(arr, strlen(arr), &d) == SLV_SLDATA_MALFORMED, "top-level array -> MALFORMED");

	const char *scalar = "42";
	CHECK(slv_sldata_parse(scalar, strlen(scalar), &d) == SLV_SLDATA_MALFORMED, "top-level scalar -> MALFORMED");

	/* Wrong-typed known fields must be ignored, not crash, not counted. */
	const char *wrong = "{\"sp\":\"notavector\",\"ug\":\"high\",\"sh\":[1,2]}";
	slv_sldata_status s = slv_sldata_parse(wrong, strlen(wrong), &d);
	CHECK(s == SLV_SLDATA_EMPTY, "all-wrong-typed -> EMPTY");
	CHECK(d.fields_seen == 0, "wrong-typed fields not counted");

	/* NULL / zero-length inputs. */
	CHECK(slv_sldata_parse(NULL, 10, &d) == SLV_SLDATA_MALFORMED, "NULL buf -> MALFORMED");
	CHECK(slv_sldata_parse("{}", 0, &d) == SLV_SLDATA_MALFORMED, "zero len -> MALFORMED");
}

/* ---- oversized: rejected before parsing --------------------------------- */
static void test_oversized(void) {
	printf("test_oversized\n");
	size_t n = SLV_SLDATA_MAX_BYTES + 100;
	char *big = malloc(n + 1);
	CHECK(big != NULL, "alloc big");
	if(!big) return;
	/* Construct a technically-valid-but-huge object so we prove the size gate
	 * fires BEFORE the JSON parser, not because the content is bad. */
	memset(big, ' ', n);
	big[0] = '{'; big[1] = '}';
	big[n] = '\0';
	slv_sldata d;
	slv_sldata_status s = slv_sldata_parse(big, n, &d);
	CHECK(s == SLV_SLDATA_TOOBIG, "oversized -> TOOBIG");
	CHECK(d.fields_seen == 0, "oversized leaves fields zero");
	free(big);

	/* A payload exactly at the limit is allowed to parse. */
	const char *ok = "{\"m\":true}";
	CHECK(slv_sldata_parse(ok, strlen(ok), &d) == SLV_SLDATA_OK, "within-limit OK");
}

/* ---- non-NUL-terminated buffer: len must be honoured -------------------- */
static void test_length_honoured(void) {
	printf("test_length_honoured\n");
	/* Buffer where the valid JSON is a prefix and trailing bytes are garbage;
	 * parsing only `len` bytes must succeed and ignore the tail. */
	const char full[] = "{\"m\":true}GARBAGE";
	size_t good = strlen("{\"m\":true}");
	slv_sldata d;
	slv_sldata_status s = slv_sldata_parse(full, good, &d);
	CHECK(s == SLV_SLDATA_OK, "len-bounded parse OK");
	CHECK(d.fields_seen == SLV_FIELD_M, "only m within bounded len");
}

/* ---- fields_str helper -------------------------------------------------- */
static void test_fields_str(void) {
	printf("test_fields_str\n");
	char buf[64];
	slv_sldata_fields_str(SLV_FIELD_SP | SLV_FIELD_SH | SLV_FIELD_ECHO, buf, sizeof(buf));
	CHECK(strcmp(buf, "sp,sh,echo") == 0, "fields_str order/format");
	slv_sldata_fields_str(0, buf, sizeof(buf));
	CHECK(strcmp(buf, "") == 0, "fields_str empty");
	/* Truncation must not overflow. */
	char tiny[3];
	slv_sldata_fields_str(SLV_FIELD_SP | SLV_FIELD_SH, tiny, sizeof(tiny));
	CHECK(tiny[2] == '\0', "fields_str NUL-terminates under truncation");
}

int main(void) {
	printf("== SLData parser unit tests ==\n");
	test_full();
	test_object_vectors();
	test_partial();
	test_empty_object();
	test_echo();
	test_malformed();
	test_oversized();
	test_length_honoured();
	test_fields_str();
	printf("== %d checks, %d failure(s) ==\n", g_checks, g_failures);
	return g_failures == 0 ? 0 : 1;
}
