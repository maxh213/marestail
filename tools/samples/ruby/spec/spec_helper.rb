require "simplecov"
SimpleCov.start do
  add_filter "/spec/"
  enable_coverage :branch
  track_files "lib/**/*.rb"
end
require_relative "../lib/box"
