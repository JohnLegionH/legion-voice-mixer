#! /bin/bash
# Run the Legion SLVoice mixer container.
# The Janus configuration files live in ./etc/janus and are mounted into the
# container. This script first rewrites values in those files from ./env and
# ./secrets (same mechanism as os-webrtc-janus-docker), then brings the
# container up detached.
set -e

if [ ! -e ./secrets ]; then
    echo "ERROR: ./secrets not found. Copy secrets.sample to secrets and set tokens."
    exit 1
fi

# Apply ./env and ./secrets to the mounted configuration files.
./updateConfiguration.sh

docker compose \
    --file docker-compose.yml \
    --env-file ./env \
    --project-name legion-voice-mixer \
    up \
    --detach
