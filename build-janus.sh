#! /bin/bash
# DEVELOPERS ONLY. Build the container image locally from source, instead of
# pulling the published one from GHCR. Operators do not need this — see the
# README "Install" section.
#
# Janus is built from the pinned git submodule (vendor/janus-gateway @ v1.4.1),
# which the Dockerfile COPYs from the build context. Init the submodule first:
#
#   git submodule update --init --recursive
#
# The image is tagged with the same name docker-compose.yml expects, so after
# building you can just `docker compose up -d` and Docker uses this local image.
set -e

IMAGE=ghcr.io/johnlegionh/legion-voice-mixer:latest
ARCH=x86_64

if [ ! -e vendor/janus-gateway/configure.ac ]; then
    echo "ERROR: vendor/janus-gateway is empty. Run: git submodule update --init --recursive"
    exit 1
fi

docker build \
    --build-arg ARCH="$ARCH" \
    -t "$IMAGE" \
    -f "Dockerfile" \
    .

echo "Built $IMAGE — now: cp env.sample .env && docker compose up -d"
