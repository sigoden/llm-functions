#!/data/data/com.termux/files/usr/bin/env bash
# @describe A video/audio downloader tool utilizing the lux CLI.
# @option --url! <TEXT> The video/playlist URL to download.
# @option --output-path <PATH> Destination directory.
# @option --info <BOOL> If true, outputs video metadata and formats without downloading.
# @option --audio-only <BOOL> If true, downloads only the best quality audio.

# Resolve lux binary
LUX_BIN=$(which lux)
if [[ -z "$LUX_BIN" ]]; then
    echo '{"success": false, "error": "lux video downloader is not installed in the system"}'
    exit 1
fi

if [[ -z "$argc_url" ]]; then
    echo "Usage: lux_download.sh --url <URL> [--output-path <PATH>] [--info] [--audio-only]"
    exit 1
fi

ARGS=()

if [[ -n "$argc_output_path" ]]; then
    mkdir -p "$argc_output_path"
    ARGS+=("-o" "$argc_output_path")
fi

if [[ "$argc_info" == "true" || "$argc_info" == "1" ]]; then
    ARGS+=("-i")
fi

if [[ "$argc_audio_only" == "true" || "$argc_audio_only" == "1" ]]; then
    ARGS+=("-ao")
fi

# Execute download
"$LUX_BIN" "${ARGS[@]}" "$argc_url"
