#!/data/data/com.termux/files/usr/bin/env bash
# @describe Run a basic network speed test using curl.
main() {
    echo "Testing download speed from Speedtest.net (10MB)..."
    curl -o /dev/null http://speedtest.tele2.net/10MB.zip
}
eval "$(argc --argc-eval "$0" "$@")"
