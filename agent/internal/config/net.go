package config

import "net"

// netSplitHostPort indirection keeps config.go's import list stable.
func netSplitHostPort(addr string) (string, string, error) {
	return net.SplitHostPort(addr)
}
