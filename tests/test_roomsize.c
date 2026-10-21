/* Copyright (c) 2026 Legion Builds
 * SPDX-License-Identifier: GPL-3.0-only
 * Licensed under the GNU General Public License, version 3 only; see the LICENSE file in the repository root.
 */
/*! \file    tests/test_roomsize.c
 * \author   Legion Voice Mixer project
 * \copyright GNU General Public License v3
 * \brief    Unit tests for the runtime room size (src/roomsize.h).
 *
 * \details  Plain C, libc only, built and run by `make test`. Covers the default, values in range
 * (both ends included), values out of range, text that is not a whole number, and a compiled maximum
 * below the default. Non-zero exit on failure.
 */

#include <stdio.h>
#include <string.h>

#include "../src/roomsize.h"

static int failures = 0;

#define CHECK(cond, what) do { if(!(cond)) { printf("FAIL: %s\n", what); failures++; } } while(0)

static void expect(const char *text, int max, slv_room_size_verdict want_v, int want_size, const char *what) {
	int size = -1;
	slv_room_size_verdict v = slv_room_size_parse(text, max, &size);
	if(v != want_v || size != want_size) {
		printf("FAIL: %s (text '%s' max %d: verdict %d size %d, want verdict %d size %d)\n",
			what, text ? text : "(null)", max, (int)v, size, (int)want_v, want_size);
		failures++;
	}
}

int main(void) {
	CHECK(SLV_ROOM_SIZE_DEFAULT == 50, "the default room size is 50");
	CHECK(SLV_ROOM_SIZE_MIN == 2, "the smallest room size is 2");

	/* default */
	expect(NULL, 110, SLV_ROOM_SIZE_UNSET, 50, "unset uses the default");
	expect("", 110, SLV_ROOM_SIZE_UNSET, 50, "empty uses the default");

	/* in range */
	expect("50", 110, SLV_ROOM_SIZE_OK, 50, "50 is accepted");
	expect("2", 110, SLV_ROOM_SIZE_OK, 2, "the lower bound is accepted");
	expect("64", 110, SLV_ROOM_SIZE_OK, 64, "64 is accepted");
	expect("110", 110, SLV_ROOM_SIZE_OK, 110, "the compiled maximum is accepted");

	/* out of range: reported, and the default is used */
	expect("1", 110, SLV_ROOM_SIZE_INVALID, 50, "1 is below the range");
	expect("0", 110, SLV_ROOM_SIZE_INVALID, 50, "0 is below the range");
	expect("-5", 110, SLV_ROOM_SIZE_INVALID, 50, "a negative value is refused");
	expect("111", 110, SLV_ROOM_SIZE_INVALID, 50, "one above the maximum is refused");
	expect("1000", 110, SLV_ROOM_SIZE_INVALID, 50, "far above the maximum is refused");
	expect("99999999999999999999", 110, SLV_ROOM_SIZE_INVALID, 50, "an overflowing number is refused");

	/* not a whole number */
	expect("abc", 110, SLV_ROOM_SIZE_INVALID, 50, "text is refused");
	expect("50x", 110, SLV_ROOM_SIZE_INVALID, 50, "trailing text is refused");
	expect("5.5", 110, SLV_ROOM_SIZE_INVALID, 50, "a fraction is refused");

	/* a build whose compiled maximum is below the default */
	expect(NULL, 32, SLV_ROOM_SIZE_UNSET, 32, "the default is capped by a smaller compiled maximum");
	expect("33", 32, SLV_ROOM_SIZE_INVALID, 32, "above a smaller compiled maximum is refused");
	expect("32", 32, SLV_ROOM_SIZE_OK, 32, "a smaller compiled maximum is accepted");

	if(failures) {
		printf("%d failure(s)\n", failures);
		return 1;
	}
	printf("test_roomsize: all passed\n");
	return 0;
}
