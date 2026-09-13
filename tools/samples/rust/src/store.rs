use crate::domain::tag;

pub struct Store {
    items: Vec<i32>,
}

impl Store {
    pub fn new(items: Vec<i32>) -> Self {
        Store { items }
    }

    pub fn size(&self) -> usize {
        return self.items.len();
    }

    pub fn first_tag(&self) -> &'static str {
        tag(self.items[0])
    }

    pub fn unused_helper(&self) -> i32 {
        1
    }
}
