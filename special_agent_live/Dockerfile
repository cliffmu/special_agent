FROM python:3.12.14-slim-bookworm

ARG BUILD_VERSION="0.1.4"
ARG BUILD_ARCH
LABEL io.hass.name="Special Agent Live" \
      io.hass.description="GPT-Live audio bridge for Home Assistant Voice PE" \
      io.hass.version="${BUILD_VERSION}" \
      io.hass.type="app" \
      io.hass.arch="${BUILD_ARCH}"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY install_source.py /tmp/install_source.py
RUN python /tmp/install_source.py /app \
    && pip install --no-cache-dir -r /app/experimental/live/requirements.txt \
    && rm /tmp/install_source.py
COPY run.py /app/run.py
EXPOSE 8099
CMD ["python", "/app/run.py"]
