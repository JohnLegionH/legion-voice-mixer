/* Copyright (c) 2026 Legion Builds
 * SPDX-License-Identifier: GPL-3.0-only
 * Licensed under the GNU General Public License, version 3 only; see the LICENSE file in the repository root.
 */
/*! \file    possync.c
 * \author   Legion Voice Mixer project
 * \copyright GNU General Public License v3
 * \brief    peer_pos parser, per-room store and observe-only comparison. See possync.h.
 */

#include <math.h>
#include <stdlib.h>
#include <string.h>

#include <jansson.h>

#include "possync.h"
#include "visauth.h"

const char *slv_pos_status_str(slv_pos_status st) {
	switch(st) {
		case SLV_POS_OK:        return "ok";
		case SLV_POS_MALFORMED: return "malformed";
		case SLV_POS_TOOBIG:    return "too_big";
		default:                return "?";
	}
}

const char *slv_pos_sim_state_str(slv_pos_sim_state s) {
	switch(s) {
		case SLV_POS_SIM_NONE:  return "none";
		case SLV_POS_SIM_STALE: return "stale";
		case SLV_POS_SIM_FRESH: return "fresh";
		default:                return "?";
	}
}

/* A canonical UUID: 8-4-4-4-12 hex digits, either case. Writes the lowercase form to out (SLV_UUID_LEN bytes). */
static int slv_pos_uuid_canon(const char *s, char out[SLV_UUID_LEN]) {
	if(s == NULL || strlen(s) != 36)
		return 0;
	for(int i = 0; i < 36; i++) {
		char c = s[i];
		if(i == 8 || i == 13 || i == 18 || i == 23) {
			if(c != '-')
				return 0;
			out[i] = c;
			continue;
		}
		if(c >= 'A' && c <= 'F')
			c = (char)(c - 'A' + 'a');
		else if(!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')))
			return 0;
		out[i] = c;
	}
	out[36] = '\0';
	return 1;
}

/* An integer within [lo, hi] (JSON integers only: a real, a string or a boolean is refused). */
static int slv_pos_int(json_t *v, int64_t lo, int64_t hi, int64_t *out) {
	if(v == NULL || !json_is_integer(v))
		return 0;
	json_int_t i = json_integer_value(v);
	if(i < lo || i > hi)
		return 0;
	*out = (int64_t)i;
	return 1;
}

/* One entry's value: an array whose first five members are integers within their bounds. */
static int slv_pos_parse_entry(const char *key, json_t *val, slv_pos_entry *e) {
	if(!slv_pos_uuid_canon(key, e->uuid) || !json_is_array(val) || json_array_size(val) < 5)
		return 0;
	return slv_pos_int(json_array_get(val, 0), -SLV_POS_COORD_MAX, SLV_POS_COORD_MAX, &e->x)
		&& slv_pos_int(json_array_get(val, 1), -SLV_POS_COORD_MAX, SLV_POS_COORD_MAX, &e->y)
		&& slv_pos_int(json_array_get(val, 2), -SLV_POS_COORD_MAX, SLV_POS_COORD_MAX, &e->z)
		&& slv_pos_int(json_array_get(val, 3), 0, SLV_POS_SPEED_MAX_CMS, &e->speed_cms)
		&& slv_pos_int(json_array_get(val, 4), 0, SLV_POS_TOL_MAX_CM, &e->tol_cm);
}

slv_pos_status slv_posbatch_parse(const char *buf, size_t len, slv_posbatch *out) {
	memset(out, 0, sizeof(*out));
	if(buf == NULL || len == 0)
		return SLV_POS_MALFORMED;
	if(len > SLV_POS_MAX_BYTES)
		return SLV_POS_TOOBIG;
	json_error_t jerr;
	json_t *root = json_loadb(buf, len, 0, &jerr);
	if(root == NULL)
		return SLV_POS_MALFORMED;
	if(!json_is_object(root)) {
		json_decref(root);
		return SLV_POS_MALFORMED;
	}
	slv_pos_status st = SLV_POS_MALFORMED;
	json_t *pos = json_object_get(root, "pos");
	json_t *full = json_object_get(root, "full");
	json_t *mode = json_object_get(root, "mode");
	json_t *epoch = json_object_get(root, "room_epoch");
	int64_t room, seq;
	if(!slv_pos_int(json_object_get(root, "room"), 0, INT64_MAX, &room)
			|| !slv_pos_int(json_object_get(root, "seq"), 1, INT64_MAX, &seq)
			|| !json_is_boolean(full) || !json_is_object(pos)
			|| (mode != NULL && !json_is_string(mode))
			|| (epoch != NULL && (!json_is_string(epoch) || !slv_vis_parse_epoch(json_string_value(epoch), &out->room_epoch))))
		goto done;
	if(json_object_size(pos) > SLV_POS_MAX_ENTRIES) {
		st = SLV_POS_TOOBIG;
		goto done;
	}
	out->room = room;
	out->seq = seq;
	out->full = json_is_true(full) ? 1 : 0;
	out->has_epoch = epoch != NULL;
	out->enforce_requested = mode != NULL && !strcmp(json_string_value(mode), "enforce");
	size_t n = json_object_size(pos);
	if(n > 0) {
		out->entries = calloc(n, sizeof(slv_pos_entry));
		if(out->entries == NULL)
			goto done;
	}
	const char *key;
	json_t *val;
	json_object_foreach(pos, key, val) {
		if(slv_pos_parse_entry(key, val, &out->entries[out->n_entries]))
			out->n_entries++;
		else
			out->n_skipped++;
	}
	st = SLV_POS_OK;
done:
	json_decref(root);
	if(st != SLV_POS_OK)
		slv_posbatch_free(out);
	return st;
}

void slv_posbatch_free(slv_posbatch *b) {
	if(b == NULL)
		return;
	free(b->entries);
	memset(b, 0, sizeof(*b));
}

/* ---- the store ---- */

static unsigned slv_pos_hash(const char *s) {
	unsigned h = 2166136261u;
	for(; *s; s++)
		h = (h ^ (unsigned char)*s) * 16777619u;
	return h;
}

/* The slot holding `uuid` (lowercase), or the empty slot it belongs in, or NULL when the table is full. */
static slv_pos_sim *slv_pos_probe(const slv_pos_store *st, const char *uuid) {
	unsigned h = slv_pos_hash(uuid);
	for(unsigned i = 0; i < SLV_POS_SLOTS; i++) {
		slv_pos_sim *s = (slv_pos_sim *)&st->slot[(h + i) & (SLV_POS_SLOTS - 1)];
		if(!s->used || !strcmp(s->e.uuid, uuid))
			return s;
	}
	return NULL;
}

void slv_pos_store_clear(slv_pos_store *st) {
	memset(st, 0, sizeof(*st));
}

int slv_pos_store_apply(slv_pos_store *st, const slv_posbatch *b, int64_t now_us, int *dropped_full) {
	int stored = 0;
	if(dropped_full != NULL)
		*dropped_full = 0;
	if(b->full)
		slv_pos_store_clear(st);
	for(int i = 0; i < b->n_entries; i++) {
		slv_pos_sim *s = slv_pos_probe(st, b->entries[i].uuid);
		if(s == NULL || (!s->used && st->count >= SLV_POS_STORE_MAX)) {
			if(dropped_full != NULL)
				(*dropped_full)++;
			continue;
		}
		if(!s->used) {
			s->used = 1;
			st->count++;
		}
		s->e = b->entries[i];
		s->recv_us = now_us;
		stored++;
	}
	return stored;
}

const slv_pos_sim *slv_pos_store_find(const slv_pos_store *st, const char *uuid) {
	char key[SLV_UUID_LEN];
	size_t n = uuid != NULL ? strlen(uuid) : 0;
	if(n == 0 || n >= SLV_UUID_LEN)
		return NULL;
	for(size_t i = 0; i <= n; i++) {
		char c = uuid[i];
		key[i] = (c >= 'A' && c <= 'Z') ? (char)(c - 'A' + 'a') : c;
	}
	const slv_pos_sim *s = slv_pos_probe(st, key);
	return (s != NULL && s->used) ? s : NULL;
}

double slv_pos_tolerance_now(const slv_pos_sim *sim, int64_t age_us) {
	if(age_us < 0)
		age_us = 0;
	return (double)sim->e.tol_cm + (double)sim->e.speed_cms * ((double)age_us / 1e6);
}

/* ---- observing ---- */

void slv_pos_room_tick_begin(slv_pos_room_stats *rs) {
	rs->would_clamp_now = 0;
	rs->would_use_sim_now = 0;
	rs->no_sim_now = 0;
}

void slv_pos_observe(slv_pos_part_stats *ps, slv_pos_room_stats *rs, const slv_pos_sim *sim, int64_t now_us,
		int has_sp, int has_lp, const double claim_sp[3]) {
	ps->ticks_observed++;
	ps->dev_cm = -1.0;
	if(sim == NULL) {
		ps->state = SLV_POS_SIM_NONE;
		ps->age_ms = -1;
		memset(&ps->sim, 0, sizeof(ps->sim));
		ps->no_sim++;
		rs->no_sim_ticks++;
		rs->no_sim_now++;
		return;
	}
	int64_t age_us = now_us - sim->recv_us;
	if(age_us < 0)
		age_us = 0;
	ps->sim = sim->e;
	ps->age_ms = age_us / 1000;
	if(age_us > (int64_t)SLV_POS_STALE_MS * 1000) {
		ps->state = SLV_POS_SIM_STALE;
		ps->no_sim++;
		ps->no_sim_stale++;
		rs->no_sim_ticks++;
		rs->no_sim_stale_ticks++;
		rs->no_sim_now++;
		return;
	}
	ps->state = SLV_POS_SIM_FRESH;
	if(!has_sp || !has_lp) {
		ps->would_use_sim++;
		rs->would_use_sim_ticks++;
		rs->would_use_sim_now++;
	}
	if(!has_sp)
		return;
	double dx = claim_sp[0] - (double)sim->e.x, dy = claim_sp[1] - (double)sim->e.y, dz = claim_sp[2] - (double)sim->e.z;
	double dev = sqrt(dx * dx + dy * dy + dz * dz);
	if(isnan(dev))
		return;   /* a claim that is not a number says nothing; the SLData parser admits only JSON numbers, so none arrives */
	ps->dev_cm = dev;
	double excess = dev - slv_pos_tolerance_now(sim, age_us);
	if(excess > 0.0) {
		ps->would_clamp++;
		rs->would_clamp_ticks++;
		rs->would_clamp_now++;
		if(dev > ps->would_clamp_max_dev_cm)
			ps->would_clamp_max_dev_cm = dev;
		if(excess > ps->would_clamp_max_excess_cm)
			ps->would_clamp_max_excess_cm = excess;
		if(dev > rs->would_clamp_max_dev_cm)
			rs->would_clamp_max_dev_cm = dev;
		if(excess > rs->would_clamp_max_excess_cm)
			rs->would_clamp_max_excess_cm = excess;
	}
}
