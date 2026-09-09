require_relative "spec_helper"

RSpec.describe Box do
  it "stores a value" do
    expect(Box.new(3).value).to eq(3)
  end
end
