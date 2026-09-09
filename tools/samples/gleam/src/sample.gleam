import sample/util

pub fn main() {
  util.identity(1)
}

pub fn choose(a: Int) -> Int {
  case a {
    1 -> 1
    2 -> 2
    _ -> 0
  }
}
