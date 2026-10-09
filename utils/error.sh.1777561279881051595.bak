#!/usr/bin/env bash

# Centralized error handling utility for Gemini CLI tools.

log_error() {
    local message="$1"
    echo -e "\033[31m[ERROR]\033[0m $message" >&2
}

log_warning() {
    local message="$1"
    echo -e "\033[33m[WARNING]\033[0m $message" >&2
}

die() {
    log_error "$1"
    exit 1
}
