package metrics

import "vpn-agent/internal/api"

// ComputeDeltas turns cumulative Xray counters into per-interval deltas.
//
// A counter that decreased (Xray restarted) or a counter seen for the first
// time is reported as its current value: no data is lost and no negative
// deltas are ever emitted. The returned baseline must be persisted by the
// caller; it drops names that disappeared from the snapshot.
func ComputeDeltas(previous, current map[string]int64) (map[string]int64, map[string]int64) {
	deltas := make(map[string]int64, len(current))
	baseline := make(map[string]int64, len(current))
	for name, value := range current {
		baseline[name] = value
		var delta int64
		if prev, ok := previous[name]; ok {
			if value >= prev {
				delta = value - prev
			} else {
				// Counter reset (xray restart): everything counted so far is new.
				delta = value
			}
		} else {
			delta = value
		}
		if delta != 0 {
			deltas[name] = delta
		}
	}
	return deltas, baseline
}

// CopyCounters returns a copy of a counters map.
func CopyCounters(counters map[string]int64) map[string]int64 {
	out := make(map[string]int64, len(counters))
	for name, value := range counters {
		out[name] = value
	}
	return out
}

// TrafficTotals sums the per-user traffic deltas into the node totals.
func TrafficTotals(deltas map[string]int64) api.Traffic {
	var total api.Traffic
	for name, delta := range deltas {
		parsed, ok := ParseStatsName(name)
		if !ok || parsed.Scope != ScopeUser {
			continue
		}
		switch parsed.Direction {
		case "uplink":
			total.Up += delta
		case "downlink":
			total.Down += delta
		}
	}
	return total
}

// ClientTraffic aggregates user deltas by client email.
func ClientTraffic(deltas map[string]int64) map[string]api.Traffic {
	clients := map[string]api.Traffic{}
	for name, delta := range deltas {
		parsed, ok := ParseStatsName(name)
		if !ok || parsed.Scope != ScopeUser || parsed.Tag == "" {
			continue
		}
		traffic := clients[parsed.Tag]
		switch parsed.Direction {
		case "uplink":
			traffic.Up += delta
		case "downlink":
			traffic.Down += delta
		}
		clients[parsed.Tag] = traffic
	}
	return clients
}

// ScopeTraffic aggregates inbound or outbound deltas by inbound/outbound tag.
func ScopeTraffic(deltas map[string]int64, scope string) map[string]api.Traffic {
	out := map[string]api.Traffic{}
	for name, delta := range deltas {
		parsed, ok := ParseStatsName(name)
		if !ok || parsed.Scope != scope || parsed.Tag == "" {
			continue
		}
		traffic := out[parsed.Tag]
		switch parsed.Direction {
		case "uplink":
			traffic.Up += delta
		case "downlink":
			traffic.Down += delta
		}
		out[parsed.Tag] = traffic
	}
	return out
}
