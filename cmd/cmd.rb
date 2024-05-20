#!/usr/bin/env ruby

require 'json'
require 'pathname'

def load_module
  if __FILE__.end_with?("cmd.rb")
    func_name = ARGV[0]
  else
    func_name = Pathname.new(__FILE__).basename.to_s
  end

  func_name += '.rb' unless func_name.end_with?('.rb')
  func_path = File.expand_path("../rb/#{func_name}", __dir__)

  begin
    return require_relative func_path
  rescue LoadError
    puts "Invalid ruby function: #{func_name}"
    exit 1
  end
end

if ENV["LLM_FUNCTION_ACTION"] == "declarate"
  declarate = load_module.method(:declarate)
  puts JSON.pretty_generate(declarate.call)
else
  begin
    data = JSON.parse(ENV["LLM_FUNCTION_DATA"])
  rescue JSON::ParserError
    puts "Invalid LLM_FUNCTION_DATA"
    exit 1
  end
  execute = load_module.method(:execute)
  execute.call(data)
end