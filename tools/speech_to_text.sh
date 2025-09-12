#!/usr/bin/env bash

# @describe Record user's voice, transcribe it via OpenAI-compatible endpoint, and use as LLM prompt input.

# @option --language[af|am|ar|as|az|ba|be|bg|bn|bo|br|bs|ca|cs|cy|da|de|el|en|es|et|eu|fa|fi|fo|fr|gl|gu|ha|haw|he|hi|hr|ht|hu|hy|id|is|it|ja|jw|ka|kk|km|kn|ko|la|lb|ln|lo|lt|lv|mg|mi|mk|ml|mn|mr|ms|mt|my|ne|nl|nn|no|oc|pa|pl|ps|pt|ro|ru|sa|sd|si|sk|sl|sn|so|sq|sr|su|sv|sw|ta|te|tg|th|tk|tl|tr|tt|uk|ur|uz|vi|yi|yo|zh|yue] The spoken language in the audio.

# @env WHISPER_API_BASE! Base URL of your OpenAI-compatible transcription API (e.g. https://api.openai.com/v1, https://speaches_domain/v1/)
# @env WHISPER_MODEL! Model name to use for transcription (e.g., "whisper-1", "Systran/faster-distil-whisper-large-v3")
# @env WHISPER_API_KEY! API key for authenticating with the transcription service
# @env LLM_OUTPUT=/dev/stdout The output path

main() {
  local model="${WHISPER_MODEL:-"whisper-1"}"
  local api_key="${WHISPER_API_KEY:-""}"
  local api_base="${WHISPER_API_BASE:-https://api.openai.com/v1}"
  api_base="${api_base%/}"

  local transcription_language="${argc_language:-"en"}"

  local audio_file="$(mktemp --suffix=.wav /tmp/llm_record_audio_XXXXXX)"
  trap "rm -f '$audio_file'" EXIT

  rec -c 1 --buffer 256 -d "$audio_file" &
  rec_pid=$!

  printf "\033[32m[Recording started — press 's' to stop or 'c' to cancel.]\033[0m\n"

  while true; do
    read -rsn1 choice
    case "$choice" in
    s | S)
      kill "${rec_pid}" 2>/dev/null
      wait "${rec_pid}" 2>/dev/null
      break
      ;;
    c | C)
      kill "${rec_pid}" 2>/dev/null
      wait "${rec_pid}" 2>/dev/null
      printf "Error: Recording cancelled; abort.\n"
      exit 1
      ;;
    *)
      :
      ;;
    esac
  done

  if ! [ -s "$audio_file" ]; then
    printf "Error: No audio recorded; abort.\n"
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
    printf "Error: Whisper API request failed with status %s\n" "$http_status"
    printf "Response: %s\n" "$http_body"
    exit 1
  fi

  transcript=$(echo "$http_body" | jq -r '.text // empty')

  if [[ -z "$transcript" ]]; then
    printf "Error: Transcript is empty or could not be parsed; abort.\n"

    exit 1
  fi

  printf "\n[User Voice Input]\n%s\n" "$transcript" >>"$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
