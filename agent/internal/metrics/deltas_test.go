package metrics

import (
	"reflect"
	"testing"

	"vpn-agent/internal/api"
)

func TestComputeDeltas(t *testing.T) {
	prev := map[string]int64{
		"user>>>a@b>>>traffic>>>uplink":   100,
		"user>>>a@b>>>traffic>>>downlink": 200,
		"old>>>counter":                   9,
	}
	current := map[string]int64{
		"user>>>a@b>>>traffic>>>uplink":         150,
		"user>>>a@b>>>traffic>>>downlink":       260,
		"user>>>c@d>>>traffic>>>uplink":         7,
		"inbound>>>vless-in>>>traffic>>>uplink": 0,
	}
	deltas, baseline := ComputeDeltas(prev, current)
	want := map[string]int64{
		"user>>>a@b>>>traffic>>>uplink":   50,
		"user>>>a@b>>>traffic>>>downlink": 60,
		"user>>>c@d>>>traffic>>>uplink":   7,
	}
	if !reflect.DeepEqual(deltas, want) {
		t.Fatalf("deltas = %v, want %v", deltas, want)
	}
	if _, ok := baseline["old>>>counter"]; ok {
		t.Errorf("baseline kept a counter that disappeared")
	}
	if baseline["inbound>>>vless-in>>>traffic>>>uplink"] != 0 {
		t.Errorf("baseline must include zero counters: %v", baseline)
	}
}

func TestComputeDeltasCounterReset(t *testing.T) {
	prev := map[string]int64{"counter": 500}
	current := map[string]int64{"counter": 30}
	deltas, baseline := ComputeDeltas(prev, current)
	if deltas["counter"] != 30 {
		t.Fatalf("reset delta = %d, want 30", deltas["counter"])
	}
	if baseline["counter"] != 30 {
		t.Fatalf("baseline = %d", baseline["counter"])
	}
}

func TestTrafficAggregation(t *testing.T) {
	deltas := map[string]int64{
		"user>>>a@b>>>traffic>>>uplink":         10,
		"user>>>a@b>>>traffic>>>downlink":       20,
		"user>>>c@d>>>traffic>>>uplink":         5,
		"user>>>c@d>>>traffic>>>downlink":       6,
		"inbound>>>vless-in>>>traffic>>>uplink": 100,
		"outbound>>>direct>>>traffic>>>uplink":  90,
	}
	totals := TrafficTotals(deltas)
	if totals.Up != 15 || totals.Down != 26 {
		t.Fatalf("totals = %+v", totals)
	}
	clients := ClientTraffic(deltas)
	if !reflect.DeepEqual(clients["a@b"], api.Traffic{Up: 10, Down: 20}) {
		t.Errorf("client a@b = %+v", clients["a@b"])
	}
	if !reflect.DeepEqual(clients["c@d"], api.Traffic{Up: 5, Down: 6}) {
		t.Errorf("client c@d = %+v", clients["c@d"])
	}
	inbound := ScopeTraffic(deltas, ScopeInbound)
	if !reflect.DeepEqual(inbound["vless-in"], api.Traffic{Up: 100}) {
		t.Errorf("inbound = %+v", inbound)
	}
	outbound := ScopeTraffic(deltas, ScopeOutbound)
	if !reflect.DeepEqual(outbound["direct"], api.Traffic{Up: 90}) {
		t.Errorf("outbound = %+v", outbound)
	}
}

func TestCopyCounters(t *testing.T) {
	original := map[string]int64{"a": 1}
	copied := CopyCounters(original)
	copied["a"] = 2
	if original["a"] != 1 {
		t.Fatalf("CopyCounters returned an alias")
	}
}
