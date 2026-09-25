#!/usr/bin/env bash
# Point the backend at a real OpenAI-compatible endpoint instead of the mock LLM.
#
# The key is read from the environment or prompted for silently -- it is never
# passed as an argument (argv is visible in `ps`) and never echoed.
#
#   ./scripts/use-real-llm.sh <model> [base_url]
#   OPENPROGRAM_LITELLM_API_KEY=sk-... ./scripts/use-real-llm.sh gpt-4.1
#
# Default base_url is https://eu.api.openai.com (no /v1 -- the adapter appends
# /v1/chat/completions itself).
set -euo pipefail

MODEL="${1:-}"
BASE_URL="${2:-https://eu.api.openai.com}"

if [ -z "$MODEL" ]; then
  echo "usage: $0 <model> [base_url]" >&2
  exit 2
fi

KEY="${OPENPROGRAM_LITELLM_API_KEY:-${OPENAI_API_KEY:-}}"
if [ -z "$KEY" ]; then
  printf 'API key for %s (not echoed): ' "$BASE_URL" >&2
  read -rs KEY
  printf '\n' >&2
fi
if [ -z "$KEY" ]; then
  echo "no key given; nothing changed" >&2
  exit 2
fi

echo "==> probing $BASE_URL/v1/chat/completions with model $MODEL"
probe=$(curl -sS -o /tmp/op-llm-probe.json -w '%{http_code}' \
  -X POST "$BASE_URL/v1/chat/completions" \
  -H "Authorization: Bearer $KEY" \
  -H 'content-type: application/json' \
  -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"reply with the single word: ok\"}]}" \
  || echo 000)
if [ "$probe" != "200" ]; then
  echo "probe failed (HTTP $probe) -- .env left untouched:" >&2
  head -c 400 /tmp/op-llm-probe.json >&2; echo >&2
  rm -f /tmp/op-llm-probe.json
  exit 1
fi
rm -f /tmp/op-llm-probe.json
echo "==> endpoint and model verified"

MODEL="$MODEL" BASE_URL="$BASE_URL" KEY="$KEY" python3 - <<'PY'
import os, pathlib, re

env = pathlib.Path(".env")
lines = env.read_text().splitlines()
managed = {
    "OPENPROGRAM_LLM_PROVIDER": "litellm",
    "OPENPROGRAM_LITELLM_MODEL": os.environ["MODEL"],
    "OPENPROGRAM_LITELLM_API_KEY": os.environ["KEY"],
    "OPENPROGRAM_LITELLM_BASE_URL": os.environ["BASE_URL"],
    "OPENPROGRAM_LITELLM_BASE_URL_INTERNAL": os.environ["BASE_URL"],
}
seen = set()
out = []
for line in lines:
    m = re.match(r"^(#?)(OPENPROGRAM_(?:LLM_PROVIDER|LITELLM_[A-Z_]+))=", line)
    if not m:
        out.append(line)
        continue
    key = m.group(2)
    if key not in managed:
        out.append(line)
        continue
    if key in seen:
        continue  # collapse the duplicate/commented variants
    seen.add(key)
    out.append(f"{key}={managed[key]}")
for key, value in managed.items():
    if key not in seen:
        out.append(f"{key}={value}")
env.write_text("\n".join(out) + "\n")
print("==> .env updated (key not printed)")
PY

echo "==> recreating backend and worker"
docker compose up -d --no-deps --force-recreate backend worker >/dev/null
until curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; do sleep 2; done
echo "==> backend up; /ready:"
curl -s http://127.0.0.1:8000/ready
echo
echo "Note: /ready reports llm_provider=false against a real endpoint -- the probe"
echo "GETs {base_url}/health/readiness, which only the LiteLLM gateway serves."
