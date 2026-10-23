package store

import "testing"

func TestSize(t *testing.T) {
	if New([]int{1, 2}).Size() != 2 {
		t.Fatal("size")
	}
}

func TestGreeting(t *testing.T) {
	t.Setenv("SAMPLE_GREETING", "hi")
	if Greeting() != "hi" {
		t.Fatal("greeting")
	}
}
