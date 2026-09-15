/*! \file    visbatch.c
 * \author   Legion Voice Mixer project
 * \copyright GNU General Public License v3
 * \brief    Implementation of the visibility-batch parser (see visbatch.h).
 */

#include "visbatch.h"
#include "visauth.h"   /* Phase 0: slv_vis_parse_epoch */

#include <jansson.h>
#include <string.h>
#include <stdlib.h>

const char *slv_vis_op_str(slv_vis_op op) {
	switch(op) {
		case SLV_VIS_OP_ADD:     return "add";
		case SLV_VIS_OP_REMOVE:  return "remove";
		case SLV_VIS_OP_REPLACE: return "replace";
		default:                 return "?";
	}
}

void slv_visbatch_free(slv_visbatch *b) {
	if(b == NULL)
		return;
	if(b->entries != NULL) {
		for(int i = 0; i < b->n_entries; i++)
			free(b->entries[i].excl);
		free(b->entries);
	}
	b->entries = NULL;
	b->n_entries = 0;
	if(b->mute_entries != NULL) {
		for(int i = 0; i < b->n_mute_entries; i++)
			free(b->mute_entries[i].excl);
		free(b->mute_entries);
	}
	b->mute_entries = NULL;
	b->n_mute_entries = 0;
	free(b->base);
	b->base = NULL;
	b->n_base = 0;
}

/* Bounded copy of a UUID string into a fixed SLV_UUID_LEN buffer (always NUL-
 * terminated). Callers pre-check length via slv_vis_uuid_ok, but bound anyway. */
static void slv_ucpy(char dst[SLV_UUID_LEN], const char *src) {
	size_t n = strlen(src);
	if(n >= SLV_UUID_LEN)
		n = SLV_UUID_LEN - 1;
	memcpy(dst, src, n);
	dst[n] = '\0';
}

/* A UUID-ish string: non-empty and short enough to store. We deliberately do
 * NOT validate the 8-4-4-4-12 shape — the mixer keys by the display string the
 * viewer chose, and an over-strict check would silently drop legitimate ids. */
static int slv_vis_uuid_ok(const char *s) {
	if(s == NULL)
		return 0;
	size_t n = strlen(s);
	return n > 0 && n < SLV_UUID_LEN;
}

/* Parse ONE per-listener channel object { "<L>":["<S>",...], ... } into a heap array of
 * slv_vis_entry. Shared by the "excl" (exclusion) and "mute" (moderation) channels so both get
 * identical resilience: over-cap or malformed listener/source items are SKIPPED and counted, the
 * rest still parse. An absent/empty channel yields NULL/0 (skew-safe: absent "mute" == no mutes).
 * Never dereferences past the json; never aborts. *n_skipped is accumulated, not reset. */
static void slv_vis_parse_channel(json_t *chan, slv_vis_entry **out_entries, int *out_n, int *n_skipped) {
	*out_entries = NULL;
	*out_n = 0;
	if(!json_is_object(chan) || json_object_size(chan) == 0)
		return;

	size_t nkeys = json_object_size(chan);
	slv_vis_entry *entries = calloc(nkeys, sizeof(slv_vis_entry));
	if(entries == NULL)
		return;   /* OOM: treat as an empty channel rather than failing the whole batch */

	int n = 0;
	const char *lkey;
	json_t *lval;
	json_object_foreach(chan, lkey, lval) {
		if(n >= SLV_VIS_MAX_ENTRIES) {
			(*n_skipped)++;
			continue;
		}
		if(!slv_vis_uuid_ok(lkey) || !json_is_array(lval)) {
			(*n_skipped)++;
			continue;
		}
		slv_vis_entry *e = &entries[n];
		slv_ucpy(e->listener, lkey);

		size_t nsrc = json_array_size(lval);
		if(nsrc > 0) {
			e->excl = calloc(nsrc, sizeof(*e->excl));
			if(e->excl == NULL) {
				(*n_skipped)++;
				continue;
			}
		}
		size_t si;
		json_t *sval;
		json_array_foreach(lval, si, sval) {
			if(e->n_excl >= SLV_VIS_MAX_EXCL) {
				(*n_skipped)++;
				continue;
			}
			if(!json_is_string(sval)) {
				(*n_skipped)++;
				continue;
			}
			const char *s = json_string_value(sval);
			if(!slv_vis_uuid_ok(s)) {
				(*n_skipped)++;
				continue;
			}
			slv_ucpy(e->excl[e->n_excl], s);
			e->n_excl++;
		}
		/* Keep the entry even if n_excl==0: for REPLACE that clears the listener's channel. */
		n++;
	}
	*out_entries = entries;
	*out_n = n;
}

slv_visbatch_status slv_visbatch_parse(const char *buf, size_t len, slv_visbatch *out) {
	if(out == NULL)
		return SLV_VISBATCH_MALFORMED;
	memset(out, 0, sizeof(*out));
	if(buf == NULL || len == 0)
		return SLV_VISBATCH_MALFORMED;
	if(len > SLV_VISBATCH_MAX_BYTES)
		return SLV_VISBATCH_TOOBIG;

	json_error_t err;
	json_t *root = json_loadb(buf, len, 0, &err);
	if(root == NULL || !json_is_object(root)) {
		if(root)
			json_decref(root);
		return SLV_VISBATCH_MALFORMED;
	}

	/* Accept the batch at top level or wrapped in "slvoice_vis". */
	json_t *b = json_object_get(root, "slvoice_vis");
	if(!json_is_object(b))
		b = root;

	/* op (required) */
	json_t *jop = json_object_get(b, "op");
	if(!json_is_string(jop)) {
		json_decref(root);
		return SLV_VISBATCH_MALFORMED;
	}
	const char *op = json_string_value(jop);
	if(!strcmp(op, "add"))
		out->op = SLV_VIS_OP_ADD;
	else if(!strcmp(op, "remove"))
		out->op = SLV_VIS_OP_REMOVE;
	else if(!strcmp(op, "replace"))
		out->op = SLV_VIS_OP_REPLACE;
	else {
		json_decref(root);
		return SLV_VISBATCH_MALFORMED;
	}

	/* room (required, integer) */
	json_t *jroom = json_object_get(b, "room");
	if(!json_is_integer(jroom)) {
		json_decref(root);
		return SLV_VISBATCH_MALFORMED;
	}
	out->room = (int64_t)json_integer_value(jroom);

	/* Phase 0 authority stamp (optional; nonspatial-phase0-design.md §1). Checked before any allocation,
	 * so a malformed stamp returns with nothing to free. */
	json_t *jepoch = json_object_get(b, "room_epoch");
	if(jepoch != NULL) {
		json_t *jgen = json_object_get(b, "policy_generation");
		if(!json_is_string(jepoch) || !slv_vis_parse_epoch(json_string_value(jepoch), &out->room_epoch)
				|| !json_is_integer(jgen) || json_integer_value(jgen) < 1
				|| json_integer_value(jgen) > (json_int_t)UINT32_MAX) {
			json_decref(root);
			memset(out, 0, sizeof(*out));
			return SLV_VISBATCH_MALFORMED;
		}
		out->has_epoch = 1;
		out->policy_generation = (int64_t)json_integer_value(jgen);
	}

	/* excl (optional object listener -> [source,...]) — the EXCLUSION (ban/visibility) channel. */
	json_t *excl = json_object_get(b, "excl");
	slv_vis_parse_channel(excl, &out->entries, &out->n_entries, &out->n_skipped);

	/* mute (optional object listener -> [source,...]) — the ADDITIVE moderation MUTE channel
	 * (Option A). Absent => n_mute_entries==0 => today's behaviour exactly. Parsed with the SAME
	 * helper so both channels share the resilience posture. This is the point that makes the
	 * mixer-parses / sim-does-not-yet-emit skew case identical to today: no "mute" key here. */
	json_t *mute = json_object_get(b, "mute");
	slv_vis_parse_channel(mute, &out->mute_entries, &out->n_mute_entries, &out->n_skipped);

	/* base (optional object listener -> generation) — only meaningful with room_epoch. A malformed item
	 * is skipped and counted; the mixer then treats that listener as a base mismatch (stale). */
	json_t *jbase = json_object_get(b, "base");
	if(out->has_epoch && json_is_object(jbase) && json_object_size(jbase) > 0) {
		size_t nb = json_object_size(jbase);
		out->base = calloc(nb, sizeof(slv_vis_base));
		if(out->base != NULL) {
			const char *bkey;
			json_t *bval;
			json_object_foreach(jbase, bkey, bval) {
				if(out->n_base >= SLV_VIS_MAX_ENTRIES * 2 || !slv_vis_uuid_ok(bkey) || !json_is_integer(bval)
						|| json_integer_value(bval) < 0 || json_integer_value(bval) > (json_int_t)UINT32_MAX) {
					out->n_skipped++;
					continue;
				}
				slv_ucpy(out->base[out->n_base].listener, bkey);
				out->base[out->n_base].gen = (int64_t)json_integer_value(bval);
				out->n_base++;
			}
		}
	}

	json_decref(root);
	/* OK if EITHER channel carried an applicable entry; a batch with neither is EMPTY. This lets a
	 * mute-ONLY op (excl:{} + mute:{...}) apply, where the pre-mute parser returned EMPTY on empty excl. */
	return (out->n_entries > 0 || out->n_mute_entries > 0) ? SLV_VISBATCH_OK : SLV_VISBATCH_EMPTY;
}
