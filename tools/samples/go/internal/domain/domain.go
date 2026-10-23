package domain

import (
	"fmt"

	"example.com/marestail/sample/internal/store"
)

// Classify names the size of n.
func Classify(n int) string {
	if n >= 10 {
		if n > 20 {
			if n > 30 {
				if n > 40 {
					return "huge"
				}
				return "big"
			}
			return "mid"
		}
		return "small"
	}
	return "tiny"
}

func Tag(n int) string {
	switch n {
	case 0:
		return "zero"
	default:
		return "other"
	}
}

func Label(s store.Store) string {
	return describe(s)
}

func describe(s store.Store) string {
	return fmt.Sprintf("box %d", s.Size())
}
