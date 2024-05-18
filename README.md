# LLM Functions

This project allows you to enhance large language models (LLMs) with custom functions written in Bash. Imagine your LLM being able to execute system commands, access web APIs, or perform other complex tasks – all triggered by simple, natural language prompts.

## Prerequisites

Make sure you have the following tools installed:

- [argc](https://github.com/sigoden/argc): A bash command-line framewrok and command runner
- [jq](https://github.com/jqlang/jq): A JSON processor
- [curl](https://curl.se): A command-line tool for transferring data with URLs 

## Getting Started with AIChat

**1. Clone the repository:**

```sh
git clone https://github.com/sigoden/llm-functions
```

**2. Build function declarations:**

Before using the functions, you need to generate a `./functions.json` file that describes the available functions for the LLM.

```sh
argc build-declarations <function_names>...
```

Replace `<function_names>...` with the actual names of your functions. Go to the [./bin](https://github.com/sigoden/llm-functions/tree/main/bin) directory for valid function names.

> 💡 You can also create  a `./functions.txt` file with each function name on a new line, Once done, simply run `argc build-declarations` without specifying the function names to automatically use the ones listed in.


**3. Configure your AIChat:**

Symlink this repo directory to aichat **functions_dir**:

```sh
ln -s "$(pwd)" "$(aichat --info | grep functions_dir | awk '{print $2}')"
# OR
argc install
```

Don't forget to add the following config to your AIChat `config.yaml` file:

```yaml
function_calling: true
```

AIChat will automatically load `functions.json` and execute functions located in the `./bin` directory based on your prompts.

**4. Start using your functions:**

Now you can interact with your LLM using natural language prompts that trigger your defined functions.

![image](https://github.com/sigoden/llm-functions/assets/4012553/867b7b2a-25fb-4c74-9b66-3701eaabbd1f)

## Writing Your Own Functions

Create a new Bash script in the `./bin` directory with the name of your function (e.g., `get_current_weather`) Follow the structure demonstrated in existing examples. For instance:

```sh
# @describe Get the current weather in a given location.
# @option --location! The city and state, e.g. San Francisco, CA

main() {
    curl "https://wttr.in/$(echo "$argc_location" | sed 's/ /+/g')?format=4&M"
}

eval "$(argc --argc-eval "$0" "$@")"
```

The relationship between flags/options and parameters in function declarations is as follows:

```sh
# @flag --boolean                   Parameter `{"type": "boolean"}`
# @option --string                  Parameter `{"type": "string"}`
# @option --string-enum[foo|bar]    Parameter `{"type": "string", "enum": ["foo", "bar"]}`
# @option --integer <INT>           Parameter `{"type": "integer"}`
# @option --number <NUM>            Parameter `{"type": "number"}`
# @option --array* <VALUE>          Parameter `{"type": "array", "items": {"type":"string"}}`
# @option --scalar-required!        Use `!` to mark a scalar parameter as required.
# @option --array-required+         Use `+` to mark a array parameter as required
```

**After creating your function, don't forget to rebuild the function declarations.**

## Function Types

### Retrieve Type

The function returns JSON data to LLM for further processing.

AIChat does not ask permission to run the function or print the output.

### Execute Type

The function does not return data to LLM. Instead, they enable more complex actions, such as showing a progress bar or running a TUI application.

AIChat will ask permission before running the function.

![image](https://github.com/sigoden/aichat/assets/4012553/711067b8-dd23-443d-840a-5556697ab075)

**AIChat categorizes functions starting with `may_` as `execute type` and all others as `retrieve type`.**

## License

The project is under the MIT License, Refer to the [LICENSE](https://github.com/sigoden/llm-functions/blob/main/LICENSE) file for detailed information.