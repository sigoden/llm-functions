#!/usr/bin/env bash
set -e

# @env LLM_OUTPUT=/dev/stdout The output path

# @cmd Create a new argc script template
# @option --name!          Script name (without .sh extension)
# @option --description!   Brief description of what the script does
# @option --type[basic|advanced|tool|agent] Template type
# @flag --with-completion  Include completion function examples
create_script() {
  local script_name="${argc_name}.sh"
  local template_type="${argc_type:-basic}"

  case "$template_type" in
  "basic")
    create_basic_template "$script_name"
    ;;
  "advanced")
    create_advanced_template "$script_name"
    ;;
  "tool")
    create_tool_template "$script_name"
    ;;
  "agent")
    create_agent_template "$script_name"
    ;;
  esac

  echo "Created argc script: $script_name" >> "$LLM_OUTPUT"
}

# @cmd Validate argc script syntax
# @arg script_path! Path to the argc script to validate
validate_script() {
  if [[ ! -f "$argc_script_path" ]]; then
    echo "Error: Script not found: $argc_script_path" >> "$LLM_OUTPUT"
    return 1
  fi

  echo "Validating argc script: $argc_script_path" >> "$LLM_OUTPUT"

  # Check for required elements
  if ! grep -q "eval.*argc.*argc-eval" "$argc_script_path"; then
    echo "Warning: Missing argc evaluation line" >> "$LLM_OUTPUT"
  fi

  # Check comment tag syntax
  grep -n "^# @" "$argc_script_path" | while read -r line; do
    echo "Found tag: $line" >> "$LLM_OUTPUT"
  done

  # Try to parse with argc
  if command -v argc >/dev/null 2>&1; then
    argc --argc-help "$argc_script_path" >/dev/null 2>&1 && echo "✓ Script parses correctly" >> "$LLM_OUTPUT" || echo "✗ Script has parsing errors" >> "$LLM_OUTPUT"
  else
    echo "Note: argc not installed, cannot validate parsing" >> "$LLM_OUTPUT"
  fi
}

# @cmd Generate completion function template
# @option --function-name! Name of the completion function
# @option --choices* Static choices (if any)
# @flag --dynamic Generate dynamic completion example
generate_completion() {
  local func_name="_choice_${argc_function_name}"

  {
    cat <<EOF
$func_name() {
EOF

    if [[ ${#argc_choices[@]} -gt 0 ]]; then
      printf '    echo "%s"\n' "${argc_choices[@]}"
    fi

    if [[ "$argc_dynamic" == "1" ]]; then
      cat <<'EOF'
    # Dynamic completion example
    case "$ARGC_CWORD" in
        *.txt|*.md)
            # Complete text files
            find . -name "*.txt" -o -name "*.md" 2>/dev/null | sed 's|^\./||'
            ;;
        *)
            # Default completion
            ls -1 2>/dev/null || true
            ;;
    esac
EOF
    fi

    echo "}"
  } >> "$LLM_OUTPUT"
}

# @cmd Show argc best practices
# @option --category[syntax|organization|validation|completion|performance] Focus area
show_best_practices() {
  local category="${argc_category:-all}"

  case "$category" in
  "syntax")
    show_syntax_practices
    ;;
  "organization")
    show_organization_practices
    ;;
  "validation")
    show_validation_practices
    ;;
  "completion")
    show_completion_practices
    ;;
  "performance")
    show_performance_practices
    ;;
  *)
    show_all_practices
    ;;
  esac
}

# @cmd Debug argc variable access
# @arg script_path! Path to argc script
debug_variables() {
  {
    echo "Debugging argc variables in: $argc_script_path"

    # Extract variable definitions
    echo "=== Defined Parameters ==="
    grep -E "^# @(option|flag|arg)" "$argc_script_path" | while read -r line; do
      echo "$line"
    done

    echo -e "\n=== Expected Variables ==="
    grep -E "^# @(option|flag|arg)" "$argc_script_path" | while read -r line; do
      case "$line" in
      *"@option"*)
        var_name=$(echo "$line" | sed -E 's/.*--([a-zA-Z0-9-]+).*/\1/' | tr '-' '_')
        echo "argc_$var_name"
        ;;
      *"@flag"*)
        var_name=$(echo "$line" | sed -E 's/.*--([a-zA-Z0-9-]+).*/\1/' | tr '-' '_')
        echo "argc_$var_name"
        ;;
      *"@arg"*)
        var_name=$(echo "$line" | awk '{print $3}')
        echo "argc_$var_name"
        ;;
      esac
    done
  } >> "$LLM_OUTPUT"
}

create_basic_template() {
  local script_name="$1"
  cat >"$script_name" <<'EOF'
#!/usr/bin/env bash
set -e

# @describe DESCRIPTION_PLACEHOLDER
# @option --input!    Input file or directory
# @option --output    Output file (default: stdout)
# @flag --verbose     Enable verbose output
# @flag --dry-run     Show what would be done without executing

main() {
    if [[ "$argc_verbose" == "1" ]]; then
        echo "Verbose mode enabled"
        echo "Input: $argc_input"
        echo "Output: ${argc_output:-stdout}"
        echo "Dry run: ${argc_dry_run:-0}"
    fi
    
    # Your code here
    echo "Processing: $argc_input"
    
    if [[ "$argc_dry_run" == "1" ]]; then
        echo "Dry run - no changes made"
        return 0
    fi
    
    # Actual processing logic
}

eval "$(argc --argc-eval "$0" "$@")"
EOF
  sed -i "s/DESCRIPTION_PLACEHOLDER/${argc_description}/" "$script_name"
  chmod +x "$script_name"
}

create_advanced_template() {
  local script_name="$1"
  cat >"$script_name" <<'EOF'
#!/usr/bin/env bash
set -e

# @describe DESCRIPTION_PLACEHOLDER
# @meta version 1.0.0
# @meta require-tools git,curl

# @cmd Process files
# @option --input!           Input directory
# @option --pattern          File pattern to match
# @option --exclude*         Patterns to exclude
# @flag --recursive          Process subdirectories
process() {
    echo "Processing files in: $argc_input"
    # Implementation here
}

# @cmd Generate report
# @option --format[json|csv|txt]  Output format
# @option --output               Output file
# @flag --summary                Include summary
report() {
    echo "Generating ${argc_format:-txt} report"
    # Implementation here
}

# @cmd Configuration management
# @cmd Set configuration value
# @arg key!     Configuration key
# @arg value!   Configuration value
config::set() {
    echo "Setting $argc_key = $argc_value"
    # Implementation here
}

# @cmd Get configuration value
# @arg key!     Configuration key
config::get() {
    echo "Getting value for: $argc_key"
    # Implementation here
}

eval "$(argc --argc-eval "$0" "$@")"
EOF
  sed -i "s/DESCRIPTION_PLACEHOLDER/${argc_description}/" "$script_name"
  chmod +x "$script_name"
}

create_tool_template() {
  local script_name="$1"
  cat >"$script_name" <<'EOF'
#!/usr/bin/env bash
set -e

# @describe DESCRIPTION_PLACEHOLDER
# @option --param1!          Required parameter
# @option --param2           Optional parameter
# @flag --enable-feature     Enable optional feature

# @env LLM_OUTPUT=/dev/stdout The output path

main() {
    # Validate inputs
    if [[ -z "$argc_param1" ]]; then
        echo "Error: param1 is required" >&2
        exit 1
    fi
    
    # Process
    local result=""
    if [[ "$argc_enable_feature" == "1" ]]; then
        result="Feature enabled with param1: $argc_param1"
    else
        result="Basic processing of: $argc_param1"
    fi
    
    if [[ -n "$argc_param2" ]]; then
        result="$result, param2: $argc_param2"
    fi
    
    # Output to LLM
    echo "$result" >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
EOF
  sed -i "s/DESCRIPTION_PLACEHOLDER/${argc_description}/" "$script_name"
  chmod +x "$script_name"
}

create_agent_template() {
  local script_name="$1"
  cat >"$script_name" <<'EOF'
#!/usr/bin/env bash
set -e

# @cmd Execute primary action
# @option --target!     Target to operate on
# @option --mode[safe|force]  Execution mode
primary_action() {
    echo "Executing primary action on: $argc_target"
    echo "Mode: ${argc_mode:-safe}"
    # Implementation here
}

# @cmd List available items
# @option --filter      Filter pattern
# @flag --detailed      Show detailed information
list_items() {
    echo "Listing items"
    if [[ -n "$argc_filter" ]]; then
        echo "Filter: $argc_filter"
    fi
    if [[ "$argc_detailed" == "1" ]]; then
        echo "Detailed view enabled"
    fi
    # Implementation here
}

# @cmd Get status
status() {
    echo "Current status:"
    # Implementation here
}

eval "$(argc --argc-eval "$0" "$@")"
EOF
  chmod +x "$script_name"
}

show_syntax_practices() {
  cat <<'EOF' >> "$LLM_OUTPUT"
=== Argc Syntax Best Practices ===

1. Comment Tag Placement:
   - Place comment tags immediately before function definitions
   - Use consistent indentation and spacing

2. Naming Conventions:
   - Use kebab-case for options: --input-file, --max-count
   - Use snake_case for function names: process_files, get_status
   - Use descriptive names that clearly indicate purpose

3. Required vs Optional:
   - Mark required parameters with ! suffix: --input!
   - Provide sensible defaults for optional parameters
   - Document default values in descriptions

4. Data Types:
   - Use <INT> for integers, <NUM> for numbers
   - Use [choice1|choice2] for enums
   - Use * for multi-value options: --exclude*

5. Documentation:
   - Always include @describe for the main function
   - Write clear, concise descriptions for all parameters
   - Include examples in comments when helpful

Good Example:
# @describe Process log files and generate reports
# @option --input!             Log file or directory to process
# @option --format[json|csv]   Output format (default: json)
# @option --exclude*           Patterns to exclude from processing
# @flag --verbose              Enable detailed output
EOF
}

show_organization_practices() {
  cat <<'EOF' >> "$LLM_OUTPUT"
=== Argc Organization Best Practices ===

1. Function Structure:
   - Use subcommands for related functionality: cmd::subcmd
   - Group related commands with common prefixes
   - Keep main() simple, delegate to specific functions

2. File Organization:
   - One main command per script for tools
   - Multiple related commands for complex CLIs
   - Separate utility functions from command functions

3. Error Handling:
   - Use 'set -e' for fail-fast behavior
   - Validate inputs early in functions
   - Provide meaningful error messages

4. Variable Naming:
   - Access argc variables consistently: $argc_variable_name
   - Convert kebab-case to snake_case: --input-file → $argc_input_file
   - Use built-in variables: $argc__args, $argc__positionals

Good Structure:
#!/usr/bin/env bash
set -e

# @describe Main tool description
# @meta version 1.0.0

# @cmd Primary command
# @option --input! Input file
cmd1() { ... }

# @cmd Secondary command  
# @option --output Output file
cmd2() { ... }

# Utility functions (no @cmd)
validate_input() { ... }

eval "$(argc --argc-eval "$0" "$@")"
EOF
}

show_validation_practices() {
  cat <<'EOF' >> "$LLM_OUTPUT"
=== Argc Validation Best Practices ===

1. Input Validation:
   - Check file existence before processing
   - Validate numeric ranges and formats
   - Ensure required dependencies are available

2. Choice Validation:
   - Use [choice1|choice2] for static choices
   - Use [`_choice_fn`] for dynamic choices
   - Validate custom input against expected patterns

3. Error Messages:
   - Provide specific, actionable error messages
   - Include suggestions for fixing issues
   - Use consistent error format

4. Environment Validation:
   - Check for required tools with @meta require-tools
   - Validate environment variables with @env
   - Handle missing dependencies gracefully

Example Validation:
main() {
    # Validate file exists
    if [[ ! -f "$argc_input" ]]; then
        echo "Error: Input file not found: $argc_input" >&2
        exit 1
    fi
    
    # Validate numeric range
    if [[ "$argc_count" -lt 1 || "$argc_count" -gt 100 ]]; then
        echo "Error: count must be between 1 and 100" >&2
        exit 1
    fi
    
    # Check required tools
    if ! command -v jq >/dev/null 2>&1; then
        echo "Error: jq is required but not installed" >&2
        exit 1
    fi
}
EOF
}

show_completion_practices() {
  cat <<'EOF' >> "$LLM_OUTPUT"
=== Argc Completion Best Practices ===

1. Choice Functions:
   - Name functions with _choice_ prefix: _choice_files
   - Return one option per line
   - Handle errors gracefully (return empty on failure)

2. Dynamic Completion:
   - Use ARGC_CWORD for context-sensitive completion
   - Consider file extensions and patterns
   - Cache expensive operations when possible

3. Performance:
   - Keep completion functions fast (< 100ms)
   - Limit output to reasonable number of options
   - Use efficient tools (avoid slow external commands)

Example Choice Function:
_choice_log_files() {
    # Complete log files in current directory
    find . -maxdepth 1 -name "*.log" 2>/dev/null | \
        sed 's|^\./||' | \
        head -20
}

_choice_git_branches() {
    # Complete git branches
    git branch --format='%(refname:short)' 2>/dev/null || true
}

# Usage in option definition:
# @option --log-file[`_choice_log_files`] Log file to process
# @option --branch[`_choice_git_branches`] Git branch to checkout
EOF
}

show_performance_practices() {
  cat <<'EOF' >> "$LLM_OUTPUT"
=== Argc Performance Best Practices ===

1. Script Startup:
   - Minimize external command calls in global scope
   - Use built-in bash features when possible
   - Cache expensive operations

2. Argument Processing:
   - Let argc handle parsing - don't re-parse arguments
   - Use argc variables directly
   - Avoid complex validation in choice functions

3. External Commands:
   - Use command -v to check tool availability
   - Prefer single command calls over loops
   - Use appropriate tools (awk/sed vs grep chains)

4. Memory Usage:
   - Avoid loading large files into variables
   - Use streaming processing when possible
   - Clean up temporary files

Fast Example:
main() {
    # Fast file processing
    if [[ "$argc_input" == *.gz ]]; then
        gunzip -c "$argc_input"
    else
        cat "$argc_input"
    fi | \
    awk '/pattern/ { print $1 }' | \
    sort -u > "${argc_output:-/dev/stdout}"
}

Slow Example (avoid):
main() {
    # Slow - loads entire file into memory
    local content=$(cat "$argc_input")
    for line in $content; do
        if echo "$line" | grep -q "pattern"; then
            echo "$line" | cut -d' ' -f1
        fi
    done | sort -u
}
EOF
}

show_all_practices() {
  show_syntax_practices
  echo
  show_organization_practices
  echo
  show_validation_practices
  echo
  show_completion_practices
  echo
  show_performance_practices
}

eval "$(argc --argc-eval "$0" "$@")"
