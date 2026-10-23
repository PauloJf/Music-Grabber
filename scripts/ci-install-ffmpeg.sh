#!/usr/bin/env bash
# Install ffmpeg on a GitHub Actions runner without hanging on a slow or stuck apt mirror
# (the plain `apt-get update && apt-get install` once sat for 25+ minutes). Each attempt is
# time-boxed and apt itself retries downloads; three attempts, then fail so the job can be re-run.
set -u
APT=(sudo apt-get -o Acquire::Retries=3 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30)
for attempt in 1 2 3; do
  if timeout 180 "${APT[@]}" update && timeout 300 "${APT[@]}" install -y --no-install-recommends ffmpeg; then
    exit 0
  fi
  echo "ffmpeg install attempt ${attempt} failed or timed out; retrying" >&2
  sleep 10
done
exit 1
