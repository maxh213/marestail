import gleeunit
import sample

pub fn main() {
  gleeunit.main()
}

pub fn choose_test() {
  let assert 0 = sample.choose(0)
  let assert 1 = sample.choose(1)
  let assert 2 = sample.choose(2)
}

pub fn main_test() {
  let assert 1 = sample.main()
}
