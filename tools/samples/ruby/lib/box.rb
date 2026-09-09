# a leftover comment
class Box
  def initialize(value)
    @value = value
  end

  def value
    read_value
  end

  def classify(n)
    if n > 10
      if n > 20
        if n > 30
          if n > 40
            "huge"
          else
            "big"
          end
        else
          "mid"
        end
      else
        "small"
      end
    else
      "tiny"
    end
  end

  private

  def read_value
    @value
  end

  def unused_helper
    1
  end
end
