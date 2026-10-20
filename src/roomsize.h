/* Copyright (c) 2026 Legion Builds
 * SPDX-License-Identifier: GPL-3.0-only
 * Licensed under the GNU General Public License, version 3 only; see the LICENSE file in the repository root.
 */
/*! \file    roomsize.h
 * \author   Legion Voice Mixer project
 * \copyright GNU General Public License v3
 * \brief    The runtime room size: how many participants one room admits.
 *
 * \details  JS_ROOM_SIZE is read once at start-up. The accepted range is SLV_ROOM_SIZE_MIN up to the
 * maximum the plugin was compiled for (SLV_MAX_MIX, which sizes the per-tick mix arrays), and the default
 * is SLV_ROOM_SIZE_DEFAULT. A value that is not a whole number in that range is reported and the default
 * is used instead.
 *
 * Header-only and libc only, so it compiles into the plugin and into tests/test_roomsize.c (`make test`).
 */

#ifndef SLV_ROOMSIZE_H
#define SLV_ROOMSIZE_H

#include <errno.h>
#include <stdlib.h>

#define SLV_ROOM_SIZE_MIN      2
#define SLV_ROOM_SIZE_DEFAULT  50

typedef enum {
	SLV_ROOM_SIZE_UNSET = 0,    /* variable absent or empty: the default */
	SLV_ROOM_SIZE_OK,           /* a whole number in range */
	SLV_ROOM_SIZE_INVALID       /* not a whole number, or outside min..max: the default is used */
} slv_room_size_verdict;

/* Parse the JS_ROOM_SIZE text. *out is the room size to use: the value when it is valid, the default
 * otherwise. `text` may be NULL. `max` is the compiled maximum. */
static inline slv_room_size_verdict slv_room_size_parse(const char *text, int max, int *out) {
	*out = SLV_ROOM_SIZE_DEFAULT > max ? max : SLV_ROOM_SIZE_DEFAULT;
	if(text == NULL || *text == '\0')
		return SLV_ROOM_SIZE_UNSET;
	char *end = NULL;
	errno = 0;
	long v = strtol(text, &end, 10);
	if(errno != 0 || end == text || *end != '\0' || v < SLV_ROOM_SIZE_MIN || v > max)
		return SLV_ROOM_SIZE_INVALID;
	*out = (int)v;
	return SLV_ROOM_SIZE_OK;
}

#endif
