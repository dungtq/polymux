FROM alpine:3.20

# docker-cli: restart the nginx-ui container after a leaf renewal (see entrypoint.sh).
# tzdata: honour TZ in log timestamps.
RUN apk add --no-cache openssl curl docker-cli bash coreutils tzdata

COPY entrypoint.sh healthcheck.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/healthcheck.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
