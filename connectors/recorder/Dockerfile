# S-CON-4 recorder peer (docs/voice/connector-build-plan.md).
FROM python:3.12-slim

# aiortc's manylinux wheels bundle the codec libraries; no apt packages needed.
# --only-binary forces wheel resolution: av occasionally publishes a release whose
# sdist appears first (14.4.0 did), and building av from source needs ffmpeg
# headers this slim image deliberately lacks.
RUN pip install --no-cache-dir --only-binary=:all: aiortc==1.13.0 aiohttp==3.11.11

RUN useradd --create-home --shell /usr/sbin/nologin recorder \
    && mkdir -p /recordings && chown recorder:recorder /recordings
USER recorder
WORKDIR /app
COPY segments.py recorder.py ./

VOLUME /recordings
ENV OUT_DIR=/recordings
CMD ["python", "-u", "recorder.py"]
