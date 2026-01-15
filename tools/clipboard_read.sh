#!/usr/bin/env bash
set -e

# @describe Read the current content of the user's system clipboard (Control+C).
# @env LLM_OUTPUT=/dev/stdout The output path

main() {
    # Check for Wayland (Bazzite/Fedora Default), macOS, then X11
    if command -v wl-paste &> /dev/null; then
        # Wayland logic
        wl-paste >> "$LLM_OUTPUT"
        
    elif command -v pbpaste &> /dev/null; then
        # macOS logic
        pbpaste >> "$LLM_OUTPUT"
        
    elif command -v xclip &> /dev/null; then
        # X11 Legacy logic
        xclip -selection clipboard -o >> "$LLM_OUTPUT"
        
    else
        echo "Error: No clipboard reading utility found. Please make sure wl-clipboard (Wayland), xclip (Linux X11), or pbpaste (macOS) is available." >> "$LLM_OUTPUT"
        exit 1
    fi
}

eval "$(argc --argc-eval "$0" "$@")"
