#! /bin/bash
# Restart the Legion SLVoice mixer container (re-applies ./env and ./secrets).
set -e
./stop-janus.sh
./run-janus.sh
