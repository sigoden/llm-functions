#!/usr/bin/env bash
set -e

# @describe Extract comprehensive metadata from media files (images, video, audio) using exiftool.
# @option --file! The path to the media file.
# @flag --specific Extract only common timestamps, duration, and geo-data to save context tokens.

# @env LLM_OUTPUT=/dev/stdout The output path

main() {
    # Check for exiftool (standard) or ffprobe (fallback for video)
    if ! command -v exiftool &> /dev/null && ! command -v ffprobe &> /dev/null; then
        echo "Error: Neither 'exiftool' nor 'ffprobe' found. Please install one." >> "$LLM_OUTPUT"
        exit 1
    fi

    local target="$argc_file"

    if command -v exiftool &> /dev/null; then
        if [[ -n "$argc_specific" ]]; then
            # Extract only "useful" fields to avoid spamming the context with binary dump info
            exiftool -common -Duration -DateCreated -GPSPosition "$target" >> "$LLM_OUTPUT"
        else
            exiftool "$target" >> "$LLM_OUTPUT"
        fi
    else
        # Fallback to ffprobe
        echo "Using ffprobe fallback..." >> "$LLM_OUTPUT"
        ffprobe -v quiet -print_format json -show_format -show_streams "$target" >> "$LLM_OUTPUT"
    fi
}

eval "$(argc --argc-eval "$0" "$@")"
