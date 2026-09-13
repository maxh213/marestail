use box_sample::domain::{classify, label, tag};
use box_sample::store::Store;

#[test]
fn classifies_the_extremes() {
    assert_eq!(classify(5), "tiny");
    assert_eq!(classify(50), "huge");
}

#[test]
fn tags_zero() {
    assert_eq!(tag(0), "zero");
}

#[test]
fn labels_a_store() {
    let store = Store::new(vec![0, 1]);
    assert_eq!(label(&store), "box 2");
    assert_eq!(store.first_tag(), "zero");
}
