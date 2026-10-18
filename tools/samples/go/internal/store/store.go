package store

import "os"

type Store struct {
	items []int
}

func New(items []int) Store {
	return Store{items:items}
}

func (s Store) Size() int {
	size := 0
	size = len(s.items)
	return size
}

func Greeting() string {
	return os.Getenv("SAMPLE_GREETING")
}

func UnusedHelper() int {
	return 1
}
