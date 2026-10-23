FROM python:3.12-slim

ARG APP_UID=10001
ARG APP_GID=10001

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=${APP_UID}:${APP_GID} app ./app

RUN if ! getent group "${APP_GID}" >/dev/null; then \
      groupadd --gid "${APP_GID}" bot; \
    fi \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" \
      --create-home --shell /usr/sbin/nologin bot \
    && mkdir -p /data \
    && chown -R "${APP_UID}:${APP_GID}" /app /data

USER ${APP_UID}:${APP_GID}

CMD ["python","app/bot.py"]
