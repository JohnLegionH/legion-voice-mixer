/*! \file    mixer.h
 * \author   Legion Voice Mixer project
 * \copyright GNU General Public License v3
 * \brief    Per-region spatial voice mixer — model declarations (design record)
 *
 * \details  This header captured the tick model BEFORE the mixer existed, to
 * agree the shape of the Phase-2 work. That model is now REALISED (Phase 2,
 * v0.6.0), but on the plugin's own `janus_slvoice_room` / `janus_slvoice_session`
 * structs in src/janus_slvoice.c rather than on the aspirational \ref slv_region
 * / \ref slv_participant types below — those structs already carry every piece
 * of per-participant state (Janus handle, negotiated Opus PT, jitter buffer,
 * codecs, data-channel state, diagnostics), so folding the tick onto them
 * avoided a parallel object graph and a risky migration of the field-proven
 * negotiation/echo code.
 *
 * What actually ships (see src/janus_slvoice.c):
 *   - one dedicated 20ms tick thread PER ROOM (janus_slvoice_room_ticker),
 *     started in janus_slvoice_room_create, stopped in the destroy paths — the
 *     "one thread per region, no shared mix thread" model below;
 *   - each tick decodes every ACTIVE participant once (DTX/VAD cull with a
 *     150ms release hold), then builds a flat N-minus-one mix per listener
 *     honouring that listener's per-source mutes/gains, encodes, and relays;
 *   - the pure summing/gain/RMS math lives in src/mixer/mix.c (Janus/Opus-free,
 *     unit-tested by tests/test_mix.c);
 *   - all buffers are preallocated at JOIN; the tick allocates nothing.
 *
 * The ::SLV_TICK_INTERVAL_MS / ::SLV_SAMPLE_RATE / ::SLV_SAMPLES_PER_TICK
 * constants below are the canonical tick parameters (mirrored in the plugin as
 * SLV_TICK_MS / SLV_RATE / SLV_FRAME_SAMPLES). The slv_region / slv_participant
 * struct + function declarations that follow are a SUPERSEDED design record,
 * kept for provenance; they are not compiled into the plugin and Phase 3
 * (spatialization) extends the shipping room/session structs, not these.
 *
 * Written against the Janus 1.4.1 plugin API; forward-declares the Janus
 * types it will need rather than pulling the whole core in here.
 */

#ifndef JANUS_SLVOICE_MIXER_H
#define JANUS_SLVOICE_MIXER_H

#include <stdint.h>
#include <glib.h>

/* Janus types used below (janus_plugin_session, janus_refcount, janus_mutex).
 * Written against the pinned Janus 1.4.1 headers. */
#include <janus/plugins/plugin.h>
#include <janus/refcount.h>
#include <janus/mutex.h>

/*! \brief Mixer tick period, in milliseconds. The per-region thread wakes on
 * this cadence to produce one mixed audio frame per active listener. 20ms is
 * the standard Opus frame duration. */
#define SLV_TICK_INTERVAL_MS   20

/*! \brief Internal mixing sample rate (Hz). Opus fullband. */
#define SLV_SAMPLE_RATE        48000

/*! \brief Samples per channel in one 20ms tick at ::SLV_SAMPLE_RATE. */
#define SLV_SAMPLES_PER_TICK   ((SLV_SAMPLE_RATE / 1000) * SLV_TICK_INTERVAL_MS)

/*! \brief A 3D position in region-local metres, as delivered by the SL
 * WebRTC voice data channel. Used for distance attenuation and stereo panning. */
typedef struct slv_position {
	float x;
	float y;
	float z;
} slv_position;

/*! \brief One participant (a single peer/handle) within a region.
 *
 * Owned by its \ref slv_region. A participant contributes one audio source
 * and is also a listener; per-tick the mixer builds a listener-specific mix
 * that excludes the participant's own contribution and applies gains derived
 * from the geometry between this participant and every other. */
typedef struct slv_participant {
	/*! \brief Stable participant id (Janus-side numeric id, mirrors audiobridge). */
	guint64 id;
	/*! \brief The Janus core handle used to relay mixed RTP back to this peer. */
	janus_plugin_session *handle;
	/*! \brief The region this participant currently belongs to (not owned). */
	struct slv_region *region;

	/*! \brief Display name / avatar identifier carried in the join request. */
	char *display;

	/*! \brief Latest spatial position from the data channel (region-local). */
	slv_position position;
	/*! \brief Facing/orientation, if provided; reserved for Phase 1 panning. */
	slv_position facing;

	/*! \brief Linear gain applied to this participant as a SOURCE (0.0–1.0). */
	float gain;
	/*! \brief Whether this participant is currently muted (no source contribution). */
	gboolean muted;

	/*! \brief Opaque per-participant jitter buffer of decoded PCM frames.
	 * Defined and managed in the Phase-1 implementation. */
	void *jitter;

	/*! \brief Refcount / lifecycle guards (mirrors the plugin session pattern). */
	volatile gint destroyed;
	janus_refcount ref; /* forward-declared in <janus/refcount.h> at build time */
} slv_participant;

/*! \brief One region = one mixing domain = one dedicated tick thread.
 *
 * A region maps 1:1 to an OpenSimulator region's spatial voice room (the same
 * deterministic room number the C# side computes today). It owns its
 * participants and the thread that ticks them. */
typedef struct slv_region {
	/*! \brief OpenSim region UUID string (for logging/diagnostics). */
	char *region_id;
	/*! \brief Numeric room id (matches the C#-side CalcRoomNumber value). */
	gint64 room;

	/*! \brief participant id (guint64) -> slv_participant*. Guarded by \c mutex. */
	GHashTable *participants;
	/*! \brief Protects \c participants and per-region mixer state. */
	janus_mutex mutex; /* forward-declared in <janus/mutex.h> at build time */

	/*! \brief The dedicated 20ms tick thread for this region. */
	GThread *ticker;
	/*! \brief Set to FALSE to ask the tick thread to drain and exit. */
	volatile gint running;
	/*! \brief Monotonic tick counter, incremented once per ::SLV_TICK_INTERVAL_MS. */
	guint64 tick_seq;

	/*! \brief Lifecycle guards. */
	volatile gint destroyed;
	janus_refcount ref;
} slv_region;

/* ---- Region lifecycle (Phase 1: to be implemented) ------------------------ */

/*! \brief Create a region mixing domain and (optionally) start its tick thread. */
slv_region *slv_region_create(const char *region_id, gint64 room);
/*! \brief Tear down a region: stop the tick thread, drop all participants. */
void slv_region_destroy(slv_region *region);
/*! \brief Start the dedicated 20ms tick thread for a region. */
gboolean slv_region_start(slv_region *region);
/*! \brief Ask the tick thread to stop and join it. */
void slv_region_stop(slv_region *region);

/* ---- Participant lifecycle (Phase 1: to be implemented) ------------------- */

/*! \brief Create a participant bound to a Janus handle. */
slv_participant *slv_participant_create(guint64 id, janus_plugin_session *handle, const char *display);
/*! \brief Destroy a participant (refcount-guarded). */
void slv_participant_destroy(slv_participant *participant);
/*! \brief Add a participant to a region (takes membership). */
gboolean slv_region_add_participant(slv_region *region, slv_participant *participant);
/*! \brief Remove a participant from a region. */
void slv_region_remove_participant(slv_region *region, slv_participant *participant);

/* ---- The tick (Phase 1: the actual mixer) --------------------------------- */

/*! \brief Produce one 20ms mixed frame per active listener and relay it.
 *
 * Called once per ::SLV_TICK_INTERVAL_MS by the region's tick thread. For each
 * listener it sums the (gained, spatialised) contributions of the other
 * participants, encodes, and relays via the Janus core. Pure declaration for
 * now; the implementation is the core of Phase 1. */
void slv_region_tick(slv_region *region);

#endif /* JANUS_SLVOICE_MIXER_H */
