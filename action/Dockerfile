FROM python:3.14-alpine3.23

# Setup base
RUN \
    apk add --no-cache \
        bash \
        git \
        php \
        php-ctype \
        php-dom \
        composer \
    \
    && pip3 install \
        --no-cache-dir \
        --prefer-binary \
        homeassistant-apps-repository-updater==1.4.1 \
    \
    && composer global require \
        --no-interaction \
        --no-cache \
        wnx/changelog-updater

ENV PATH="/root/.config/composer/vendor/bin:$PATH"

COPY entrypoint.sh /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
