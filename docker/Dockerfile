FROM python:3.13-alpine3.21

# Setup base
RUN \
    apk add --no-cache \
        git \
    \
    && pip3 install \
        --no-cache-dir \
        --prefer-binary \
        homeassistant-apps-repository-updater==1.4.0 \

# Entrypoint
ENTRYPOINT ["repository-updater"]

# Labels
LABEL \
    maintainer="Franck Nijhof <frenck@addons.community>" \
    org.opencontainers.image.title="Repository Updater" \
    org.opencontainers.image.description="Tool for automatically updating Home Assistant app (add-on) repositories." \
    org.opencontainers.image.vendor="Unofficial Home Assistant Apps (Add-ons)" \
    org.opencontainers.image.authors="Franck Nijhof <frenck@addons.community>" \
    org.opencontainers.image.licenses="MIT" \
    org.opencontainers.image.url="https://github.com/homeassistant-apps/repository" \
    org.opencontainers.image.source="https://github.com/homeassistant-apps/action-repository-updater" \
    org.opencontainers.image.documentation="https://github.com/homeassistant-apps/action-repository-updater/blob/main/README.md"
