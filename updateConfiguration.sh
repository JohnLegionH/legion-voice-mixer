#! /bin/bash
# Read in the parameter files ("env" and "secrets") and update
#    values in the ./etc/janus configuration files to use those values.
#
# This is functionally identical to os-webrtc-janus-docker's script (same
# sed edits, same keys) so an operator's existing env/secrets work unchanged.
# The slvoice plugin config (janus.plugin.slvoice.jcfg) needs no substitution
# at this phase, so it is not touched here.

CONFIGDIR=./etc/janus

source ./env
source ./secrets

sed --in-place \
    -e "s/#*api_secret = \".*\"/api_secret = \"${JS_API_TOKEN}\"/" \
    -e "s/#*admin_secret = \".*\"/admin_secret = \"${JS_ADMIN_TOKEN}\"/" \
    -e "s/#*server_name = \".*\"/server_name = \"${JS_SERVER_NAME:-GridVoice}\" /" \
    "${CONFIGDIR}/janus.jcfg"

# TRANSPORT.HTTP
sed --in-place \
    -e "s/#*http =.*#/http = ${JS_TRANSPORT_HTTP_ENABLE:-false} #/" \
    -e "s/#*port =.*#/port = ${JS_TRANSPORT_HTTP_PORT:-8088} #/" \
    -e "s/#*base_path = \".*\"/base_path = \"${JS_TRANSPORT_HTTP_BASEPATH}\"/" \
    -e "s/#*https =.*#/https = ${JS_TRANSPORT_HTTPS_ENABLE:-false} #/" \
    -e "s/#*http_port =.*#/http_port = ${JS_TRANSPORT_HTTPS_PORT:-8089} #/" \
    -e "s/#*admin_http =.*#/admin_http = ${JS_TRANSPORT_HTTP_ADMIN:-false} #/" \
    -e "s/#*admin_port =.*#/admin_port = ${JS_TRANSPORT_HTTP_ADMIN_PORT:-7088} #/" \
    -e "s/#*admin_base_path = \".*\"/admin_base_path = \"${JS_TRANSPORT_HTTP_ADMIN_BASEPATH:-\/admin}\"/" \
    "${CONFIGDIR}/janus.transport.http.jcfg"

# TRANSPORT.WEBSOCKETS
sed --in-place \
    -e "s/#*ws =.*#/ws = ${JS_TRANSPORT_WEBSOCKETS_WS:-false} #/" \
    -e "s/#*ws_port =.*#/ws_port = ${JS_TRANSPORT_WEBSOCKETS_WS_PORT:-8188} #/" \
    -e "s/#*wss =.*#/wss = ${JS_TRANSPORT_WEBSOCKETS_WSS:-false}/ #" \
    -e "s/#*wss_port =.*#/wss_port = ${JS_TRANSPORT_WEBSOCKETS_WSS_PORT:-8989} #/" \
    "${CONFIGDIR}/janus.transport.websockets.jcfg"

# TRANSPORT.NANOMSG
sed --in-place \
    -e "s/#*enabled =.*#/enabled = ${JS_TRANSPORT_NANOMSG_ENABLE:-false} #/" \
    "${CONFIGDIR}/janus.transport.nanomsg.jcfg"
