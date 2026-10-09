#!/data/data/com.termux/files/usr/bin/bash
# setup-claude-termux.sh
# Configure Claude Code in Termux to use Ollama or NVIDIA NIM via claude-code-router.

set -euo pipefail

info() { printf '\e[1;34m[*]\e[0m %s\n' "$*"; }
warn() { printf '\e[1;33m[!]\e[0m %s\n' "$*"; }
die()  { printf '\e[1;31m[ERROR]\e[0m %s\n' "$*" >&2; exit 1; }

[[ -n "${PREFIX:-}" ]] || die "Run this inside Termux."

MODE="${1:-}"
OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5-coder:7b}"

NIM_BASE_URL="${NIM_BASE_URL:-https://integrate.api.nvidia.com/v1}"
NIM_MODEL="${NIM_MODEL:-meta/llama-3.1-8b-instruct}"

if [[ -z "$MODE" ]]; then
  if [[ -t 0 ]]; then
    read -rp "Choose backend [ollama/nim]: " MODE
  else
    die "Usage: ./setup-claude-termux.sh [ollama|nim]"
  fi
fi

MODE="${MODE,,}"
case "$MODE" in
  ollama|nim) ;;
  *) die "Backend must be ollama or nim" ;;
esac

info "Updating Termux package index"
pkg update -y

info "Installing dependencies"
pkg install -y nodejs-lts curl ca-certificates procps \
  || pkg install -y nodejs curl ca-certificates procps

info "Installing Claude Code"
if ! command -v claude >/dev/null 2>&1; then
  npm install -g @anthropic-ai/claude-code
fi

info "Installing claude-code-router"
if ! command -v ccr >/dev/null 2>&1; then
  npm install -g @musistudio/claude-code-router
fi

mkdir -p "$HOME/bin"

if ! grep -q 'export PATH="$HOME/bin:$PATH"' "$HOME/.bashrc" 2>/dev/null; then
  echo 'export PATH="$HOME/bin:$PATH"' >> "$HOME/.bashrc"
fi

export PATH="$HOME/bin:$PATH"

write_router_config() {
  local provider="$1"
  local base_url="$2"
  local model="$3"
  local api_key="$4"

  PROVIDER="$provider" BASE_URL="$base_url" MODEL="$model" API_KEY="$api_key" node -e '
const fs = require("fs");
const os = require("os");
const path = require("path");

const provider = process.env.PROVIDER;
let base = process.env.BASE_URL.trim().replace(/\/+$/, "");
const model = process.env.MODEL;
const apiKey = process.env.API_KEY || "not-needed";

if (/\/chat\/completions$/.test(base)) {
  // already full OpenAI chat endpoint
} else if (/\/v1$/.test(base)) {
  base += "/chat/completions";
} else {
  base += "/v1/chat/completions";
}

const cfg = {
  Providers: [
    {
      name: provider,
      api_base_url: base,
      api_key: apiKey,
      models: [model],
      transformer: {
        use: ["openai"]
      }
    }
  ],
  Router: {
    default: `${provider},${model}`,
    background: `${provider},${model}`,
    thinker: `${provider},${model}`,
    longContext: `${provider},${model}`
  }
};

const dir = path.join(os.homedir(), ".claude-code-router");
fs.mkdirSync(dir, { recursive: true, mode: 0o700 });

const file = path.join(dir, "config.json");
fs.writeFileSync(file, JSON.stringify(cfg, null, 2));

try {
  fs.chmodSync(file, 0o600);
} catch {}

console.log("Wrote " + file);
'
}

create_wrapper() {
  cat > "$HOME/bin/claude-local" <<'EOF'
#!/data/data/com.termux/files/usr/bin/bash
export PATH="$HOME/bin:$PATH"
export ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-local-router}"
exec ccr code "$@"
EOF

  chmod +x "$HOME/bin/claude-local"
}

if [[ "$MODE" == "ollama" ]]; then
  if [[ -t 0 ]]; then
    read -rp "Ollama base URL [$OLLAMA_URL]: " ans
    OLLAMA_URL="${ans:-$OLLAMA_URL}"

    read -rp "Ollama model [$OLLAMA_MODEL]: " ans
    OLLAMA_MODEL="${ans:-$OLLAMA_MODEL}"
  fi

  info "Ollama URL: $OLLAMA_URL"
  info "Model: $OLLAMA_MODEL"

  if curl -fsS "$OLLAMA_URL/api/version" >/dev/null 2>&1; then
    info "Ollama is reachable."

    if command -v termux-wake-lock >/dev/null 2>&1; then
      termux-wake-lock || true
    fi

    info "Pulling model if not already present..."
    curl -fsS "$OLLAMA_URL/api/pull" \
      -H 'Content-Type: application/json' \
      --data "{\"name\":\"$OLLAMA_MODEL\"}" \
      || warn "Model pull failed. If the model already exists, you can continue."
  else
    warn "Ollama is not reachable at $OLLAMA_URL yet."
    warn "Start Ollama first, or point OLLAMA_URL at a remote Ollama server."
  fi

  write_router_config "ollama" "$OLLAMA_URL" "$OLLAMA_MODEL" "ollama"
fi

if [[ "$MODE" == "nim" ]]; then
  if [[ -t 0 ]]; then
    read -rp "NIM base URL [$NIM_BASE_URL]: " ans
    NIM_BASE_URL="${ans:-$NIM_BASE_URL}"

    read -rp "NIM model [$NIM_MODEL]: " ans
    NIM_MODEL="${ans:-$NIM_MODEL}"
  fi

  if [[ -z "${NIM_API_KEY:-}" ]]; then
    if [[ -t 0 ]]; then
      read -rsp "NVIDIA NIM API key: " NIM_API_KEY
      echo
    else
      die "Set NIM_API_KEY for non-interactive use."
    fi
  fi

  [[ -n "$NIM_API_KEY" ]] || die "NIM_API_KEY is required."

  info "NIM URL: $NIM_BASE_URL"
  info "Model: $NIM_MODEL"

  MODELS_URL="$(NIM_BASE_URL="$NIM_BASE_URL" node -e '
let b = process.env.NIM_BASE_URL.trim().replace(/\/+$/, "");

if (/\/chat\/completions$/.test(b)) {
  b = b.replace(/\/chat\/completions$/, "/models");
} else if (/\/v1$/.test(b)) {
  b += "/models";
} else {
  b += "/v1/models";
}

console.log(b);
')"

  info "Testing $MODELS_URL"

  if curl -fsS -H "Authorization: Bearer $NIM_API_KEY" "$MODELS_URL" >/dev/null 2>&1; then
    info "NIM endpoint responded."
  else
    warn "Could not list NIM models. Check URL/key/network. Continuing anyway."
  fi

  write_router_config "nim" "$NIM_BASE_URL" "$NIM_MODEL" "$NIM_API_KEY"
fi

create_wrapper

info "Done."

cat <<'DONE'

Use:

  source ~/.bashrc
  claude-local

Alternative:

  ccr code

Switch backend later by rerunning:

  ./setup-claude-termux.sh ollama
  ./setup-claude-termux.sh nim

Environment overrides:

  OLLAMA_URL
  OLLAMA_MODEL
  NIM_BASE_URL
  NIM_MODEL
  NIM_API_KEY

DONE
