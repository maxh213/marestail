package domain

import (
	"testing"

	"example.com/marestail/sample/internal/store"
)

func TestClassifyExtremes(t *testing.T) {
	if Classify(5) != "tiny" {
		t.Fatal("5")
	}
	if Classify(50) != "huge" {
		t.Fatal("50")
	}
}

func TestTagZero(t *testing.T) {
	if Tag(0) != "zero" {
		t.Fatal("0")
	}
}

func TestLabel(t *testing.T) {
	if Label(store.New([]int{0, 1})) != "box 2" {
		t.Fatal("label")
	}
}
