#!/data/data/com.termux/files/usr/bin/env bash
# @describe Check the HTTP status code and response headers of a URL.
# @arg url! The URL to check.
main() {
    # Curl's -I flag only fetches headers, which are typically not colorized by curl itself.
    # No direct color flag is applicable here without significantly changing the output format.
    curl -I -s "$argc_url" | head -n 20
}
eval "$(argc --argc-eval "$0" "$@")"
