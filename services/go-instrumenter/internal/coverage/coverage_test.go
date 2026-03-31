package coverage

import "testing"

func TestParseCoverFunc(t *testing.T) {
	output := `auth/service.go:12:	Authenticate		100.0%
auth/service.go:30:	ValidateToken		80.0%
handlers/order.go:15:	CreateOrder		0.0%
total:			(statements)		60.0%
`
	funcs := ParseCoverFunc(output)

	if len(funcs) != 3 {
		t.Fatalf("expected 3 functions, got %d", len(funcs))
	}
	if !funcs["auth/service.go:Authenticate"] {
		t.Error("Authenticate should be marked as covered")
	}
	if funcs["handlers/order.go:CreateOrder"] {
		t.Error("CreateOrder at 0.0% should not be covered")
	}
}
