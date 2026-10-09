#!/data/data/com.termux/files/usr/bin/env bash
# @describe Get weather forecast for a city (wttr.in).
# @arg city! The city name.
main() {
    curl -s "https://wttr.in/${argc_city}?format=3"
}
eval "$(argc --argc-eval "$0" "$@")"
