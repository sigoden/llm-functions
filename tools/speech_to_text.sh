#!/usr/bin/env bash
set -e

# @describe Record user's voice, transcribe it via OpenAI-compatible endpoint, and use as LLM prompt input.
# @env WHISPER_API_BASE! Base URL of your OpenAI-compatible transcription API (e.g. https://api.openai.com/v1, https://speaches_domain/v1/)
# @env WHISPER_MODEL! Model name to use for transcription (e.g., "whisper-1", "Systran/faster-distil-whisper-large-v3")
# @env WHISPER_API_KEY! API key for authenticating with the transcription service
# @env LLM_OUTPUT=/dev/stdout The output path

main() {
  local audio_file="$(mktemp --suffix=.wav /tmp/llm_record_audio_XXXXXX)"
  trap "rm -f '$audio_file'" EXIT

  local launcher="${argc_launcher:-}"
  if [[ "$launcher" == "rofi" ]]; then
    launcher="rofi -dmenu"
  elif [[ -z "$launcher" ]]; then
    if command -v dmenu >/dev/null 2>&1; then
      launcher="dmenu"
    elif command -v rofi >/dev/null 2>&1; then
      launcher="rofi -dmenu"
    else
      echo "Error: neither dmenu nor rofi found; please install one or specify --launcher"
      exit 1
    fi
  fi

  # arecord -c 1 -f S16_LE -r 48000 -d 3600 "$audio_file"
  # ffmpeg -y -f avfoundation -i :0 -t 3600 "$audio_file"
  rec -c 1 --buffer 256 -d "$audio_file" &
  rec_pid=$!

  choice=$(echo -e "Stop Recording\nCancel" | $launcher -p "Speech to Text")

  kill "${rec_pid}"
  wait "${rec_pid}"

  if [[ "${choice}" != "Stop Recording" ]]; then
    echo "Error: speech to text cancelled"
    exit 1
  fi

  if ! [ -s "$audio_file" ]; then
    echo "Error: No audio recorded; abort."
    exit 1
  fi

  response="$(curl -sS -w "%{http_code}" -X POST "${WHISPER_API_BASE%/}/audio/transcriptions" \
    -H "Authorization: Bearer ${WHISPER_API_KEY:-}" \
    -F model="${WHISPER_MODEL:-'whisper-1'}" \
    -F language="en" \
    -F file=@"$audio_file")"
  http_body="${response::-3}"   # all except last 3 chars
  http_status="${response: -3}" # last 3 chars

  if [[ "$http_status" != "200" ]]; then
    echo "Whisper API request failed with status $http_status"
    echo "Response: $http_body"
    exit 1
  fi

  transcript=$(echo "$http_body" | jq -r '.text // empty')

  if [[ -z "$transcript" ]]; then
    echo "Error: Transcript is empty or could not be parsed"
    exit 1
  fi

  printf "\n[User Voice Input]\n%s\n" "$transcript" >>"$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
