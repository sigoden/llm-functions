#!/usr/bin/env bash

# @describe Record user's voice, transcribe it via OpenAI-compatible endpoint, and use as LLM prompt input.

# @option --launcher The launcher to use (accepted values: dmenu, rofi).
# @option --language The spoken language in the audio (accepted language codes: af, am, ar, as, az, ba, be, bg, bn, bo, br, bs, ca, cs, cy, da, de, el, en, es, et, eu, fa, fi, fo, fr, gl, gu, ha, haw, he, hi, hr, ht, hu, hy, id, is, it, ja, jw, ka, kk, km, kn, ko, la, lb, ln, lo, lt, lv, mg, mi, mk, ml, mn, mr, ms, mt, my, ne, nl, nn, no, oc, pa, pl, ps, pt, ro, ru, sa, sd, si, sk, sl, sn, so, sq, sr, su, sv, sw, ta, te, tg, th, tk, tl, tr, tt, uk, ur, uz, vi, yi, yo, zh, yue).

# @env WHISPER_API_BASE! Base URL of your OpenAI-compatible transcription API (e.g. https://api.openai.com/v1, https://speaches_domain/v1/)
# @env WHISPER_MODEL! Model name to use for transcription (e.g., "whisper-1", "Systran/faster-distil-whisper-large-v3")
# @env WHISPER_API_KEY! API key for authenticating with the transcription service
# @env LLM_OUTPUT=/dev/stdout The output path

main() {
  local model="${WHISPER_MODEL:-"whisper-1"}"
  local api_key="${WHISPER_API_KEY:-""}"
  local api_base="${WHISPER_API_BASE:-https://api.openai.com/v1}"
  api_base="${api_base%/}"

  local launcher="${argc_launcher:-}"
  local transcription_language="${argc_language:-"en"}"

  local audio_file="$(mktemp --suffix=.wav /tmp/llm_record_audio_XXXXXX)"
  trap "rm -f '$audio_file'" EXIT

  if [[ "$launcher" == "rofi" ]]; then
    launcher="rofi -dmenu"
  elif [[ -z "$launcher" ]]; then
    if command -v dmenu >/dev/null 2>&1; then
      launcher="dmenu"
    elif command -v rofi >/dev/null 2>&1; then
      launcher="rofi -dmenu"
    else
      echo -e "Error: neither dmenu nor rofi found; please install one or specify --launcher\n"
      exit 1
    fi
  fi

  rec -c 1 --buffer 256 -d "$audio_file" &
  rec_pid=$!

  choice=$(echo -e "Stop Recording\nCancel" | $launcher -p "Speech to Text")

  kill "${rec_pid}"
  wait "${rec_pid}"

  if [[ "${choice}" != "Stop Recording" ]]; then
    echo -e "Error: Recording cancelled; abort.\n"
    exit 1
  fi

  if ! [ -s "$audio_file" ]; then
    echo -e "Error: No audio recorded; abort.\n"
    exit 1
  fi

  response="$(
    curl -sS -w "%{http_code}" -X POST "${api_base}/audio/transcriptions" \
      -H "Authorization: Bearer ${api_key}" \
      -F model="${model}" \
      -F language="${transcription_language}" \
      -F file=@"$audio_file"
  )"
  http_body="${response::-3}"   # all except last 3 chars
  http_status="${response: -3}" # last 3 chars

  if [[ "$http_status" != "200" ]]; then
    echo -e "Error: Whisper API request failed with status $http_status\n"
    echo -e "Response: $http_body\n"
    exit 1
  fi

  transcript=$(echo "$http_body" | jq -r '.text // empty')

  if [[ -z "$transcript" ]]; then
    echo -e "Error: Transcript is empty or could not be parsed; abort.\n"
    exit 1
  fi

  printf "\n[User Voice Input]\n%s\n" "$transcript" >>"$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
