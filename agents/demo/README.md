# Demo

This is demo agent.

## tools.{sh,js,py}

You only need one of the `tools.sh`, `tools.js`, or `tools.py` files. All three are provided so that everyone can understand how to implement the tools in each language.

## tools.txt

The `tools.txt` is used to reuse the tools in the `tools/` directory.

## index.yaml

This document is essential as it defines the agent.

### variables

Variables are generally used to record a certain behavior or preference of a user.

```yaml
variables:
  - name: foo
    description: This is a foo
  - name: bar
    description: This is a bar with default value
    default: val
```

Variables can be used in the `instructions`.

```yaml
instructions: |
  The instructions can inline {{foo}} and {{bar}} variables.
```

### documents

Documents are used for RAG.

```yaml
documents:
  - https://raw.githubusercontent.com/sigoden/llm-functions/main/README.md
```