#!/usr/bin/env bash
set -e

# @describe Demonstrate how to create a tool using Bash and how to use comment tags.
# @option --string!                  Define a required string property
# @option --string-enum![foo|bar]    Define a required string property with enum
# @option --string-optional          Define a optional string property
# @flag --boolean                    Define a boolean property
# @option --integer! <INT>           Define a required integer property
# @option --number! <NUM>            Define a required number property
# @option --array+ <VALUE>           Define a required string array property
# @option --array-optional*          Define a optional string array property

# @env LLM_OUTPUT=/dev/stdout The output path

main() {
    cat <<EOF >> "$LLM_OUTPUT"
string: ${argc_string}
string_enum: ${argc_string_enum}
string_optional: ${argc_string_optional}
boolean: ${argc_boolean}
integer: ${argc_integer}
number: ${argc_number}
array: ${argc_array[@]}
array_optional: ${argc_array_optional[@]}
$(printenv | grep '^LLM_')
EOF
}

eval "$(argc --argc-eval "$0" "$@")"
