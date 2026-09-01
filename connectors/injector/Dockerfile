# S-CON-6 injector peer (docs/voice/connector-build-plan.md).
# Build context is connectors/ (compose sets it) so the shared common/ package
# ships in the image alongside the injector package.
FROM python:3.12-slim

# Same pinned stack as the recorder; see that Dockerfile for the --only-binary
# rationale (av sdist trap).
RUN pip install --no-cache-dir --only-binary=:all: aiortc==1.13.0 aiohttp==3.11.11

RUN useradd --create-home --shell /usr/sbin/nologin injector \
    && mkdir -p /recordings /injector && chown injector:injector /recordings /injector
USER injector
WORKDIR /app
COPY common/ ./common/
COPY injector/__init__.py injector/injector.py injector/source.py ./injector/

VOLUME /recordings
ENV OUT_DIR=/recordings \
    SOURCE=/injector/in.wav
# -m so /app (cwd) is on sys.path and the common package resolves.
CMD ["python", "-u", "-m", "injector.injector"]
