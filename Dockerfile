FROM python:3.13-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN groupadd --gid 10001 iptv && useradd --uid 10001 --gid 10001 --no-create-home iptv && mkdir /data && chown iptv:iptv /data
COPY --chown=iptv:iptv mijntv /app/mijntv
USER 10001:10001
EXPOSE 8080
CMD ["python", "-m", "mijntv", "--admin-only", "--data-dir", "/data"]
