# Argcfile

The [Argcfile.sh](https://github.com/sigoden/llm-functions/blob/main/Argcfile.sh) is a powerful Bash script designed to streamline the process of managing LLM functions and agents in your AIChat environment.

We encourage running `Argcfile.sh` using `argc`. Because `argc` provides better autocompletion, it can also be used without trouble on Windows.

Argcfile.sh is to argc what Makefile is to make.

https://github.com/user-attachments/assets/1acef548-4735-49c1-8f60-c4e0baf528de

## Usage

```sh
# -------- Help --------
argc -h                                     # Print help information
argc <command> -h                           # Print help information for <command>

# -------- Build --------
# Build
argc build

# Build all tools
argc build@tool 
# Build specific tools
argc build@tool get_current_weather.sh execute_command.sh 

# Build all agents
argc build@agent 
# Build specific agents
argc build@agent coder todo

# -------- Run --------
# Run tool
argc run@tool get_current_weather.sh '{"location":"London"}'
# Run agent tool
argc run@agent todo add_todo '{"desc":"Watch a movie"}'

# -------- Test --------
# Test all
argc test
# Test tools
argc test@tool
# Test agents
argc test@agent

# -------- Clean --------
# Clean all
argc clean
# Clean tools
argc clean@tool
# Clean agents
argc clean@agent

# -------- Link --------
argc link-web-search web_search_tavily.sh 
argc link-code-interpreter execute_py_code.py 

# -------- Misc --------
# Install this repo to aichat functions_dir 
argc install                      
# Displays version information for required tools
argc version
```

## MCP Usage

```sh
# Start/restart the mcp bridge server
argc mcp start

# Stop the mcp bridge server
argc mcp stop

# Run the mcp tool
argc mcp run@tool fs_read_file '{"path":"/tmp/file1"}'

# Show the logs
argc mcp logs
```
