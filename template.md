# ==============================================================================
# PURIFIED UNIVERSAL MACRO TEMPLATE
# For sigoden/aichat (v0.27.0+) — Termux & Shell Sanctuary
# Location: ~/.config/aichat/macros/universal-master.yaml
# ==============================================================================

# ------------------------------------------------------------------------------
# SECTION 1: YAML Anchors (Reusable Arcane Components)
# ------------------------------------------------------------------------------
shell_environment: &inspect_env
  - .file `pwd && uname -a && date` -- illuminate local realm context

file_context: &load_files
  - .file {{input_file}} -- channel wisdom from designated target

role_invocation: &set_role
  - .role {{role_name}} -- cast specialized cognitive persona

model_selection: &set_model
  - .model {{model_name}} -- tune the underlying LLM conduit

save_artifact: &persist_output
  - .save {{output_file}} -- crystallize output into memory archive

# ------------------------------------------------------------------------------
# SECTION 2: Macro Execution Steps (Sequential Ritual)
# ------------------------------------------------------------------------------
steps:
  # 1. Environment & target file inspection
  - *inspect_env
  - *load_files

  # 2. Persona & Model Tuning
  - *set_role

  # 3. Core Prompt & Instruction Synthesis
  - |
    # SYSTEM INSTRUCTIONS & TASK
    {{main_prompt}}

    ---
    ## EXECUTION CONTEXT
    - Working Directory Context: Attached above
    - Additional Notes: {{notes}}
    
    ## EXTRA PARAMETERS / SPELL ARGUMENTS
    {{extra_args}}

    ---
    Please provide structured, step-by-step findings, clear bash/shell code blocks where applicable, and recommendations optimized for standard terminal execution.

  # 4. Optional Shell Action Execution (if specified)
  - .shell "{{post_command}}"

  # 5. Persist Results
  - *persist_output

# ------------------------------------------------------------------------------
# SECTION 3: Variable Definitions (The Customizable Essences)
# ------------------------------------------------------------------------------
variables:
  - name: role_name
    default: "general-assistant"
    description: "The AI role persona to invoke for this task"

  - name: input_file
    default: "package.json"
    description: "Target file or command output to include via .file"

  - name: main_prompt
    default: "Analyze the provided context, identify potential issues, and deliver an actionable summary."
    description: "Primary objective or instruction set"

  - name: notes
    default: "Executed within Termux environment."
    description: "Supplemental environment or task context"

  - name: post_command
    default: "echo '✨ Macro execution completed successfully.'"
    description: "Shell command executed via .shell after response"

  - name: output_file
    default: "macro_report.md"
    description: "File path to preserve final LLM output"

  - name: extra_args
    rest: true
    default: ""
    description: "Captures all trailing unstructured command-line inputs"
