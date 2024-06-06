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
  func_path = File.expand_path("../tools/#{func_file}", __dir__)

  begin
    require func_path
  rescue LoadError
    puts "Invalid function: #{func_file}"
    exit 1
  end
end

def load_env(file_path)
  return unless File.exist?(file_path)

  File.readlines(file_path).each do |line|
    line = line.strip
    next if line.empty? || line.start_with?('#')

    key, *value = line.split('=', 2)
    ENV[key.strip] = value.join('=').strip
  end
rescue StandardError
end

ENV['LLM_FUNCTIONS_DIR'] = Pathname.new(__dir__).join('..').expand_path.to_s

load_env(Pathname.new(ENV['LLM_FUNCTIONS_DIR']).join('.env').to_s)

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