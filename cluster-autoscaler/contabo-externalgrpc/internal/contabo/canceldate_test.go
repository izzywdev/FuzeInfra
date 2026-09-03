package contabo

import (
	"testing"
	"time"
)

// Contabo returns cancelDate as a DATE, not a timestamp: "2026-09-14". Before this
// layout was accepted, every cancelled instance parsed as unparsable and CancelDate
// stayed zero, so callers could not distinguish a cancelled instance from a live one.
//
// That kept three cancelled instances occupying slots in the elastic node group. The
// group read as full at MAX_SIZE=4 with one real node, cluster-autoscaler logged
// "Skipping node group elastic - max size reached" / "No expansion options", and 27
// runner pods sat Pending. Observed in prod 2026-09-02.
func TestParseContaboTimeAcceptsDateOnly(t *testing.T) {
	got, ok := parseContaboTime("2026-09-14")
	if !ok {
		t.Fatalf("date-only cancelDate must parse; this is the exact string the API returns")
	}
	want := time.Date(2026, 9, 14, 0, 0, 0, 0, time.UTC)
	if !got.Equal(want) {
		t.Fatalf("got %v, want %v (UTC midnight: Contabo cancels at end of billing day, so midnight is the conservative anchor)", got, want)
	}
}

func TestParseContaboTimeStillAcceptsRFC3339(t *testing.T) {
	for _, s := range []string{"2026-09-14T10:30:00Z", "2026-09-14T10:30:00.123456Z"} {
		if _, ok := parseContaboTime(s); !ok {
			t.Fatalf("RFC3339 form %q must keep parsing", s)
		}
	}
}

func TestParseContaboTimeRejectsGarbage(t *testing.T) {
	// Must stay false: a silent zero-value on real garbage is what made the original
	// bug invisible, so the caller has to be able to tell "absent" from "unparsable".
	for _, s := range []string{"", "not-a-date", "14/09/2026"} {
		if _, ok := parseContaboTime(s); ok {
			t.Fatalf("%q must not parse", s)
		}
	}
}
