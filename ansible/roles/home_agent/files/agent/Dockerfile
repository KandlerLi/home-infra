FROM python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --requirement requirements.txt

COPY home_agent ./home_agent

# Deterministic, world-readable/traversable regardless of whatever mode
# the build context happened to check out with -- COPY otherwise
# preserves the source tree's own file modes verbatim. home-infra's own
# local build never surfaced this: its build context reaches the
# homeserver via Ansible's copy task with mode: preserve, off the
# laptop's own normally-permissioned checkout. The self-hosted CI
# runner's fresh git checkout produced different (non-world-readable)
# modes, which only broke once a real non-root UID opened these files --
# confirmed live in the k3s Pod, which runs as UID 10001:
# "PermissionError: [Errno 13] Permission denied:
# '/app/home_agent/api.py'". docker build itself never caught it either,
# since it runs as root, which ignores permission bits entirely.
RUN chmod -R a+rX /app

EXPOSE 8000

CMD ["python", "-m", "home_agent.api"]
