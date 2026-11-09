package metrics

import (
	"reflect"
	"testing"
)

func TestParseStatsQueryRealShape(t *testing.T) {
	raw := []byte(`{"stat": [
		{"name": "inbound>>>socks-in>>>traffic>>>uplink"},
		{"name": "outbound>>>direct>>>traffic>>>uplink", "value": 158},
		{"name": "user>>>client-1@vpn>>>traffic>>>downlink", "value": "1234567890123"}
	]}`)
	stats, err := ParseStatsQuery(raw)
	if err != nil {
		t.Fatalf("ParseStatsQuery: %v", err)
	}
	if stats["inbound>>>socks-in>>>traffic>>>uplink"] != 0 {
		t.Errorf("missing value must parse as zero")
	}
	if stats["outbound>>>direct>>>traffic>>>uplink"] != 158 {
		t.Errorf("numeric value = %d", stats["outbound>>>direct>>>traffic>>>uplink"])
	}
	if stats["user>>>client-1@vpn>>>traffic>>>downlink"] != 1234567890123 {
		t.Errorf("int64 string value lost precision: %d", stats["user>>>client-1@vpn>>>traffic>>>downlink"])
	}
}

func TestParseStatsQueryEmptyAndArrays(t *testing.T) {
	empty, err := ParseStatsQuery([]byte("{}"))
	if err != nil || len(empty) != 0 {
		t.Fatalf("empty = %v, %v", empty, err)
	}
	fromArray, err := ParseStatsQuery([]byte(`[{"name":"n","value":2}]`))
	if err != nil || fromArray["n"] != 2 {
		t.Fatalf("array = %v, %v", fromArray, err)
	}
	single, err := ParseStatsQuery([]byte(`{"name":"x","value":3}`))
	if err != nil || single["x"] != 3 {
		t.Fatalf("single = %v, %v", single, err)
	}
	if _, err := ParseStatsQuery([]byte("not json")); err == nil {
		t.Fatalf("expected an error for invalid JSON")
	}
}

func TestParseStatsName(t *testing.T) {
	cases := []struct {
		name      string
		scope     string
		tag       string
		direction string
		ok        bool
	}{
		{"user>>>a@b>>>traffic>>>uplink", ScopeUser, "a@b", "uplink", true},
		{"inbound>>>vless-in>>>traffic>>>downlink", ScopeInbound, "vless-in", "downlink", true},
		{"outbound>>>exit-sg>>>traffic>>>uplink", ScopeOutbound, "exit-sg", "uplink", true},
		{"user>>>a@b>>>online", "", "", "", false},
		{"user>>>a@b>>>traffic>>>sideways", "", "", "", false},
		{"garbage", "", "", "", false},
	}
	for _, tc := range cases {
		parsed, ok := ParseStatsName(tc.name)
		if ok != tc.ok {
			t.Errorf("%s ok = %v", tc.name, ok)
			continue
		}
		if ok && (parsed.Scope != tc.scope || parsed.Tag != tc.tag || parsed.Direction != tc.direction) {
			t.Errorf("%s parsed = %+v", tc.name, parsed)
		}
	}
}

func TestParseOnlineUsersShapes(t *testing.T) {
	cases := []struct {
		name   string
		raw    string
		emails []string
		ips    []string
	}{
		{
			name:   "users array",
			raw:    `{"users":[{"email":"a@b","ips":["203.0.113.7","198.51.100.9"]}]}`,
			emails: []string{"a@b"},
			ips:    []string{"198.51.100.9", "203.0.113.7"},
		},
		{
			name:   "online key map",
			raw:    `{"user>>>a@b>>>online":{"203.0.113.7":1731096000}}`,
			emails: []string{"a@b"},
			ips:    []string{"203.0.113.7"},
		},
		{
			name:   "name ips object",
			raw:    `{"name":"user>>>a@b>>>online","ips":{"203.0.113.7":1}}`,
			emails: []string{"a@b"},
			ips:    []string{"203.0.113.7"},
		},
		{
			name:   "plain array of emails",
			raw:    `["a@b","c@d"]`,
			emails: []string{"a@b", "c@d"},
		},
		{
			name:   "all online users",
			raw:    `{"users":[{"email":"a@b"},{"email":"c@d"}]}`,
			emails: []string{"a@b", "c@d"},
		},
		{
			name: "empty",
			raw:  `{}`,
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			online := ParseOnlineUsers([]byte(tc.raw))
			emails := online.SortedEmails()
			if !reflect.DeepEqual(emails, tc.emails) {
				t.Fatalf("emails = %v, want %v", emails, tc.emails)
			}
			if len(tc.ips) > 0 {
				if got := online.CloneIPs(tc.emails[0]); !reflect.DeepEqual(got, tc.ips) {
					t.Fatalf("ips = %v, want %v", got, tc.ips)
				}
			}
		})
	}
}

func TestParseSysStats(t *testing.T) {
	stats := ParseSysStats([]byte(`{"Alloc":2370088,"NumGoroutine":12,"Uptime":7}`))
	if stats.Goroutines != 12 || stats.UptimeMinutes != 7 {
		t.Fatalf("stats = %+v", stats)
	}
	if empty := ParseSysStats([]byte("garbage")); empty.Goroutines != 0 {
		t.Fatalf("garbage = %+v", empty)
	}
}

func TestParseXrayVersion(t *testing.T) {
	output := []byte("Xray 26.11.9 (Xray, Penetrates Everything.) 676eaf6 (go1.27.3 linux/amd64)\nA unified platform for anti-censorship.\n")
	if got := ParseXrayVersion(output); got != "26.11.9" {
		t.Fatalf("version = %q", got)
	}
	if got := ParseXrayVersion([]byte("garbage")); got != "" {
		t.Fatalf("garbage version = %q", got)
	}
}
