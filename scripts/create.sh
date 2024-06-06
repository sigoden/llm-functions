#!/usr/bin/env bash
set -e

# @describe Create a boilplate tool script file.
# @arg name! The script filename.
# @arg params* The script parameters

main() {
    ext="${argc_name##*.}"
    output="tools/$argc_name"
    if [[ -f "$output" ]]; then
        _die "$output already exists"
    fi
    case $ext in
    sh) create_sh ;;
    js) create_js ;;
    py) create_py ;;
    *) _die "Invalid extension name: '$ext'" ;; 
    esac
}

create_sh() {
    cat <<-'EOF' | sed 's/__DESCRIBE_TAG__/# @describe/g' > "$output"
#!/usr/bin/env bash
set -e

__DESCRIBE_TAG__
EOF
    for param in "${argc_params[@]}"; do
        echo "# @option --$(echo $param | sed 's/-/_/g')" >> "$output"
    done
    cat <<-'EOF' >> "$output"

main() {
    ( set -o posix ; set ) | grep ^argc_ # inspect argc_* variables
}

eval "$(argc --argc-eval "$0" "$@")"
EOF
    chmod +x "$output"
}

create_js() {
    cat <<EOF > "$output"
exports.declarate = function declarate() {
  return $(build_schema)
}

exports.execute = function execute(data) {
  console.log(data)
}
EOF
}

create_py() {
    cat <<EOF > "$output"
def declarate():
  return $(build_schema) 


def execute(data):
  print(data)
EOF
}

build_schema() {
    echo '{
        "name": "'"${argc_name%%.*}"'",
        "description": "",
        "parameters": '"$(build_properties)"'
    }' | jq '.' | sed '2,$s/^/  /g'
}

build_properties() {
    required_params=()
    properties=''
    for param in "${argc_params[@]}"; do
        if [[ "$param" == *'!' ]]; then
            param="${param::-1}"
            required_params+=("$param")
            property='{"'"$param"'":{"type":"string","description":""}}'
        elif [[ "$param" == *'+' ]]; then
            param="${param::-1}"
            required_params+=("$param")
            property='{"'"$param"'":{"type":"array","description":"","items": {"type":"string"}}}'
        elif [[ "$param" == *'*' ]]; then
            param="${param::-1}"
            property='{"'"$param"'":{"type":"array","description":"","items": {"type":"string"}}}'
        else
            property='{"'"$param"'":{"type":"string","description":""}}'
        fi
        properties+="$property"
    done
    required=''
    for param in "${required_params[@]}"; do
        if [[ -z "$required" ]]; then
            required=',"required":['
        fi
        required+="\"$param\","
    done
    if [[ -n "$required" ]]; then
        required="${required::-1}"
        required+="]"
    fi
    echo '{
        "type": "object",
        "properteis": '"$(echo "$properties" | jq -s 'add')$required"'
    }' | jq '.'
}

_die() {
    echo "$*"
    exit 1
}

# See more details at https://github.com/sigoden/argc
eval "$(argc --argc-eval "$0" "$@")"