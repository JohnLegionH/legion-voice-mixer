#! /bin/bash
# Stop the Legion SLVoice mixer container.
docker compose \
    --file docker-compose.yml \
    --env-file ./env \
    --project-name legion-voice-mixer \
    down
