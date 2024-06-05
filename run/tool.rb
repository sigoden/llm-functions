#!/usr/bin/env ruby

require 'json'
require 'pathname'

def parse_argv
  func_file = __FILE__
    func_data = nil

  if func_file.end_with?("tool.rb")
    func_file = ARGV[0]
    func_data = ARGV[1]
  else
    func_file = File.basename(func_file)
    func_data = ARGV[0]
  end

  func_file += '.rb' unless func_file.end_with?(".rb")

  [func_file, func_data]
end

def load_func(func_file)
  func_path = File.expand_path("../tools/rb/#{func_file}", __dir__)

  begin
    require func_path
  rescue LoadError
    puts "Invalid function: #{func_file}"
    exit 1
  end
end

func_file, func_data = parse_argv

if ENV["LLM_FUNCTION_ACTION"] == "declarate"
  load_func(func_file)
  puts JSON.pretty_generate(declarate)
else
  if func_data.nil?
    puts "No json data"
    exit 1
  end

  begin
    args = JSON.parse(func_data)
  rescue JSON::ParserError
    puts "Invalid json data"
    exit 1
  end

  load_func(func_file)
  execute(args)
end