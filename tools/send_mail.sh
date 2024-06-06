#!/usr/bin/env bash
set -e

# @describe Send a email.
# @meta require-tools mutt
# @option --recipient The recipient of the email.
# @option --subject The subject of the email.
# @option --body The body of the email.

main() {
    mutt -s "$argc_subject" "$argc_recipient" <<<"$argc_body"
}

eval "$(argc --argc-eval "$0" "$@")"

