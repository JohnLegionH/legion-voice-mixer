#! /bin/bash
# Build the Legion SLVoice mixer Docker image (Janus + janus.plugin.slvoice).
#
# Unlike os-webrtc-janus-docker, this does NOT pass JANUS_GIT_REPO/BRANCH: Janus
# is built from the pinned git submodule (vendor/janus-gateway @ v1.4.1), which
# the Dockerfile COPYs from the build context. Make sure the submodule is
# checked out first:
#
#   git submodule update --init --recursive
#
set -e

BUILD_DATE=$(date "+%Y%m%d.%H%M")
BUILD_DAY=$(date "+%Y%m%d")

ARCH=x86_64

IMAGE_OWNER=legion
IMAGE_NAME=legion-voice-mixer
IMAGE_VERSION=latest

if [ ! -e vendor/janus-gateway/configure.ac ]; then
    echo "ERROR: vendor/janus-gateway is empty. Run: git submodule update --init --recursive"
    exit 1
fi

docker build \
    --build-arg BUILD_DATE="$BUILD_DATE" \
    --build-arg BUILD_DAY="$BUILD_DAY" \
    --build-arg ARCH="$ARCH" \
    --build-arg IMAGE_OWNER="$IMAGE_OWNER" \
    --build-arg IMAGE_NAME="$IMAGE_NAME" \
    --build-arg IMAGE_VERSION="$IMAGE_VERSION" \
    -t "${IMAGE_NAME}:${IMAGE_VERSION}" \
    -f "Dockerfile" \
    .
