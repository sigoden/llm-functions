#!/data/data/com.termux/files/usr/bin/env bash
# @describe Generate thumbnails from video files or URLs using ffmpeg
# @option --input* Path to the input video file or remote URL (can be specified multiple times)
# @option --output_dir "thumbnails" Output directory for the images
# @option --interval 10 Interval in seconds between thumbnails
# @option --width 320 Width of the thumbnails (height scaled automatically)
# @option --format "png" Output format (png or jpg)
# @option --start "00:00:00" Start time (e.g., 00:00:10)
# @option --end "00:00:00" Duration or end time (e.g., 00:00:20)
# @option --max_frames "10" Maximum number of frames to generate
# @option --montage "NxM" Create a montage with NxM grid (e.g., 2x3)
# @flag --add-timestamps Add timestamps to thumbnails

set -euo pipefail

main() {
    SCRIPT_PATH="${HOME}/gen_thumbs.py"
    if [[ ! -f "${SCRIPT_PATH}" ]]; then
        echo "ERROR: gen_thumbs.py script not found at ${SCRIPT_PATH}" >&2
        exit 1
    fi

    # Build python command
    cmd="python3 ${SCRIPT_PATH} ${argc_input[@]}"
    cmd+=" --output_dir ${argc_output_dir}"
    cmd+=" --interval ${argc_interval}"
    cmd+=" --width ${argc_width}"
    cmd+=" --format ${argc_format}"

    [[ -n "${argc_start:-}" && "${argc_start}" != "00:00:00" ]] && cmd+=" --start ${argc_start}"
    [[ -n "${argc_end:-}" && "${argc_end}" != "00:00:00" ]] && cmd+=" --end ${argc_end}"
    [[ -n "${argc_max_frames:-}" ]] && cmd+=" --max_frames ${argc_max_frames}"
    [[ -n "${argc_montage:-}" ]] && cmd+=" --montage ${argc_montage}"
    [[ "${argc_add_timestamps:-}" == "1" ]] && cmd+=" --add-timestamps"

    echo "Executing: ${cmd}" >&2
    eval "${cmd}"
}

eval "$(argc --argc-eval "$0" "$@")"
