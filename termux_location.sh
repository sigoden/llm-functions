#!/usr/bin/env bash
set -euo pipefail

# @describe Advanced location engine using Termux:API for high-accuracy geo-tracking.

DATA_FILE="$HOME/.termux_saved_locations.json"

# Helper: Safely validate required packages and local workspace
_init_check() {
    if ! command -v termux-location >/dev/null 2>&1; then
        echo '{"status": "error", "message": "termux-api package is not installed."}' >> "$LLM_OUTPUT"
        exit 1
    fi
    if ! command -v jq >/dev/null 2>&1; then
        echo '{"status": "error", "message": "jq package is not installed."}' >> "$LLM_OUTPUT"
        exit 1
    fi
    if [ ! -f "$DATA_FILE" ] || [ ! -s "$DATA_FILE" ]; then
        echo "{}" > "$DATA_FILE"
    fi
}

# @cmd Get the current live GPS location with automated network cell tower fallback.
get_live() {
    _init_check
    local geo_json
    # Pull satellite data first; fall back immediately to network tower triangulation on timeout or indoor environments
    if ! geo_json=$(termux-location -p gps -r last 2>/dev/null); then
        geo_json=$(termux-location -p network 2>/dev/null || echo '{"error": "Failed to get location from providers"}')
    fi
    echo "$geo_json" >> "$LLM_OUTPUT"
}

# @cmd Save a new location or update an existing one with precise coordinates.
# @option --name! The label or identity for this location (e.g., 'Office').
# @option --lat! The latitude coordinate decimal value.
# @option --lon! The longitude coordinate decimal value.
save() {
    _init_check
    local tmp_file
    tmp_file=$(mktemp)

    jq --arg name "$argc_name" 
       --arg lat "$argc_lat" 
       --arg lon "$argc_lon" 
       --arg time "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" 
       '. + {($name): {latitude: ($lat|tonumber), longitude: ($lon|tonumber), saved_at: $time}}' 
       "$DATA_FILE" > "$tmp_file"

    mv "$tmp_file" "$DATA_FILE"
    jq -n --arg name "$argc_name" '{"status": "success", "message": ("Saved location: " + $name)}' >> "$LLM_OUTPUT"
}

# @cmd Recall a previously saved location profile by its label name.
# @option --name! The exact label name of the saved location.
recall() {
    _init_check
    local loc_data
    loc_data=$(jq -r --arg name "$argc_name" '.[$name]' "$DATA_FILE")

    if [ "$loc_data" = "null" ]; then
        jq -n --arg name "$argc_name" 
              --argjson list "$(jq 'keys' "$DATA_FILE")" 
              '{"status": "error", "message": ("Location " + $name + " not found."), "available": $list}' >> "$LLM_OUTPUT"
    else
        jq --arg name "$argc_name" '{name: $name} + .[$name]' "$DATA_FILE" >> "$LLM_OUTPUT"
    fi
}

# @cmd List all previously stored locations saved in the device database.
list() {
    _init_check
    if [ "$(jq 'length' "$DATA_FILE")" -eq 0 ]; then
        echo '{"status": "empty", "message": "No locations have been saved yet."}' >> "$LLM_OUTPUT"
    else
        jq 'to_entries | map({name: .key} + .value)' "$DATA_FILE" >> "$LLM_OUTPUT"
    fi
}

# @cmd Directly launch a map application overlay using Android Intents.
# @option --lat! Target latitude coordinate decimal value.
# @option --lon! Target longitude coordinate decimal value.
open_map() {
    if ! command -v termux-open-url >/dev/null 2>&1; then
        echo '{"status": "error", "message": "termux-open-url command is not available."}' >> "$LLM_OUTPUT"
        exit 1
    fi
    # Fires a global Android intent allowing the OS to route coordinates to Google Maps, OsmAnd, or Apple Maps
    termux-open-url "geo:$argc_lat,$argc_lon?q=$argc_lat,$argc_lon"
    jq -n --arg lat "$argc_lat" --arg lon "$argc_lon" '{"status": "success", "message": ("Opened intent geo at " + $lat + "," + $lon)}' >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
