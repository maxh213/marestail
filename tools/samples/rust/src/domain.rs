// a leftover comment
use crate::store::Store;

pub fn classify(n: i32) -> &'static str {
    if n >= 10 {
        if n > 20 {
            if n > 30 {
                if n > 40 {
                    "huge"
                } else {
                    "big"
                }
            } else {
                "mid"
            }
        } else {
            "small"
        }
    } else {
        "tiny"
    }
}

pub fn tag(n: i32) -> &'static str {
    match n {
        0 => "zero",
        _ => "other",
    }
}

pub fn label(store: &Store) -> String {
    describe(store)
}

fn describe(store: &Store) -> String {
    format!("box {}", store.size())
}
