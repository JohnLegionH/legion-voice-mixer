/*! \file    deferred.c
 * \author   Legion Voice Mixer project
 * \copyright GNU General Public License v3
 * \brief    Implementation of the per-room deferred visibility/mute store (see deferred.h).
 */

#include "deferred.h"

#include <string.h>
#include <stdlib.h>

/* Bounded copy of a UUID string into a fixed SLV_UUID_LEN buffer (always NUL-
 * terminated). Mirrors slv_ucpy in visbatch.c so this module stays libc-only. */
static void slv_dcpy(char dst[SLV_UUID_LEN], const char *src) {
	size_t n = strlen(src);
	if(n >= SLV_UUID_LEN)
		n = SLV_UUID_LEN - 1;
	memcpy(dst, src, n);
	dst[n] = '\0';
}

void slv_deferred_init(slv_deferred_store *st) {
	if(st != NULL)
		memset(st, 0, sizeof(*st));
}

static void entry_free_cols(slv_deferred_entry *e) {
	free(e->excl);
	e->excl = NULL;
	e->n_excl = 0;
	free(e->mute);
	e->mute = NULL;
	e->n_mute = 0;
}

void slv_deferred_free_all(slv_deferred_store *st) {
	if(st == NULL)
		return;
	for(int i = 0; i < st->n; i++)
		entry_free_cols(&st->entries[i]);
	st->n = 0;
	/* counters retained (cumulative since init, like the room vis_* counters) */
}

int slv_deferred_count(const slv_deferred_store *st) {
	return st != NULL ? st->n : 0;
}

static int find_idx(const slv_deferred_store *st, const char *listener) {
	for(int i = 0; i < st->n; i++)
		if(strcmp(st->entries[i].listener, listener) == 0)
			return i;
	return -1;
}

const slv_deferred_entry *slv_deferred_get(const slv_deferred_store *st, const char *listener) {
	if(st == NULL || listener == NULL)
		return NULL;
	int i = find_idx(st, listener);
	return i < 0 ? NULL : &st->entries[i];
}

/* Remove the record at index i: free its columns and compact by moving the last
 * record into the hole (order is irrelevant — oldest is tracked by added_seq, not
 * array position). The vacated last slot is zeroed so its (moved-out) pointers are
 * not double-freed. */
static void remove_at(slv_deferred_store *st, int i) {
	entry_free_cols(&st->entries[i]);
	if(i != st->n - 1)
		st->entries[i] = st->entries[st->n - 1];
	memset(&st->entries[st->n - 1], 0, sizeof(st->entries[0]));
	st->n--;
}

/* Is `s` present in the first `n` rows of `col`? */
static int col_has(char (*col)[SLV_UUID_LEN], int n, const char *s) {
	for(int i = 0; i < n; i++)
		if(strcmp(col[i], s) == 0)
			return 1;
	return 0;
}

/* Update one channel in place per op (REPLACE=set exactly, ADD=union, REMOVE=subtract).
 * An empty result frees the column to NULL/0. Never partially applies: on a malloc
 * failure the channel is left unchanged. */
static void channel_update(char (**col)[SLV_UUID_LEN], int *n_col,
		slv_vis_op op, char (*src)[SLV_UUID_LEN], int n_src) {
	if(op == SLV_VIS_OP_REMOVE) {
		if(*n_col == 0)
			return;   /* nothing deferred to subtract from */
		int w = 0;
		for(int i = 0; i < *n_col; i++) {
			if(!col_has(src, n_src, (*col)[i])) {
				if(w != i)
					memcpy((*col)[w], (*col)[i], SLV_UUID_LEN);
				w++;
			}
		}
		*n_col = w;
		if(w == 0) {
			free(*col);
			*col = NULL;
		}
		return;
	}

	if(op == SLV_VIS_OP_ADD && *n_col > 0) {
		/* Union: existing rows, plus each source not already present (and de-duped
		 * within src itself). Rebuild into a right-sized array. */
		int add_n = 0;
		for(int j = 0; j < n_src; j++)
			if(!col_has(*col, *n_col, src[j]) && !col_has(src, j, src[j]))
				add_n++;
		if(add_n == 0)
			return;
		int total = *n_col + add_n;
		char (*merged)[SLV_UUID_LEN] = malloc((size_t)total * SLV_UUID_LEN);
		if(merged == NULL)
			return;   /* leave the channel unchanged on OOM */
		int w = 0;
		for(int i = 0; i < *n_col; i++)
			memcpy(merged[w++], (*col)[i], SLV_UUID_LEN);
		for(int j = 0; j < n_src; j++)
			if(!col_has(*col, *n_col, src[j]) && !col_has(src, j, src[j]))
				slv_dcpy(merged[w++], src[j]);
		free(*col);
		*col = merged;
		*n_col = w;
		return;
	}

	/* ADD into an empty channel, or REPLACE: set the channel to src EXACTLY
	 * (last-write-wins). An empty src clears the channel. */
	free(*col);
	*col = NULL;
	*n_col = 0;
	if(n_src <= 0)
		return;
	char (*fresh)[SLV_UUID_LEN] = malloc((size_t)n_src * SLV_UUID_LEN);
	if(fresh == NULL)
		return;
	int w = 0;
	for(int j = 0; j < n_src; j++)
		if(!col_has(fresh, w, src[j]))   /* de-dupe within src */
			slv_dcpy(fresh[w++], src[j]);
	*col = fresh;
	*n_col = w;
}

void slv_deferred_put(slv_deferred_store *st, const char *listener, slv_vis_op op,
		char (*src)[SLV_UUID_LEN], int n_src, int is_mute) {
	if(st == NULL || listener == NULL)
		return;
	int i = find_idx(st, listener);
	if(i < 0) {
		/* A REMOVE, or an empty column, for a listener with no existing record has
		 * nothing to defer — do not create (and do not count) a record. */
		if(op == SLV_VIS_OP_REMOVE || n_src <= 0)
			return;
		if(st->n >= SLV_VIS_MAX_DEFERRED) {
			/* Cap reached: evict the OLDEST record (lowest added_seq) and count it. */
			int oldest = 0;
			for(int k = 1; k < st->n; k++)
				if(st->entries[k].added_seq < st->entries[oldest].added_seq)
					oldest = k;
			remove_at(st, oldest);
			st->evicted++;
		}
		i = st->n++;
		memset(&st->entries[i], 0, sizeof(st->entries[i]));
		slv_dcpy(st->entries[i].listener, listener);
		st->entries[i].added_seq = ++st->seq;
		st->adds++;
	} else {
		/* A newer batch entry updates an existing deferred record. */
		st->replaced++;
	}
	slv_deferred_entry *e = &st->entries[i];
	if(is_mute)
		channel_update(&e->mute, &e->n_mute, op, src, n_src);
	else
		channel_update(&e->excl, &e->n_excl, op, src, n_src);
	/* Both channels empty => nothing to replay => clear the record. */
	if(e->n_excl == 0 && e->n_mute == 0)
		remove_at(st, i);
}

int slv_deferred_replay_done(slv_deferred_store *st, const char *listener) {
	if(st == NULL || listener == NULL)
		return 0;
	int i = find_idx(st, listener);
	if(i < 0)
		return 0;
	remove_at(st, i);
	st->replayed++;
	return 1;
}
