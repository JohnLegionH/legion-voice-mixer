# Release notes

Each release lists its **Behaviour changes on upgrade** and **One-time migrations**, even when a list is empty
(the compatibility rule in `docs/docker-notes.md`). The configuration knobs and their defaults are in the knob
register in `docs/docker-notes.md`.

## Unreleased

### Behaviour changes on upgrade

- The image is built without Janus's stock plugins and without the nanomsg and Unix-socket transports. It loads
  `janus.plugin.slvoice` only. A deployment that uses another stock plugin through this image must stay on the
  previous image for that.

### New opt-in options (defaults unchanged)

- `JS_TLS_CERT` / `JS_TLS_KEY` serve the API and admin API over https; `JS_HTTP_PLAIN` / `JS_ADMIN_HTTP_PLAIN`
  control the plain-http listeners.
- `JS_WEAK_SECRET_FATAL` makes a start-up WARNING about a short API, admin or join-capability secret fatal.
  The WARNING itself is new: a non-empty secret under 32 characters.

With none of the new options set, the generated Janus configuration is byte-identical to the previous release's.

### One-time migrations

None.
