package layers

import (
	"fmt"
	"strings"

	querypb "github.com/tersecontext/tc/services/serializer/gen"
)

// RenderWarnings writes Layer 2 (warnings) to buf.
// Writes nothing if warnings is empty.
func RenderWarnings(buf *strings.Builder, warnings []*querypb.SubgraphWarning, ids map[string]string) {
	if len(warnings) == 0 {
		return
	}
	for _, w := range warnings {
		srcID := ids[w.Source]
		switch w.Type {
		case "CONFLICT":
			tgtID := ids[w.Target]
			fmt.Fprintf(buf, "CONFLICT  %s -> %s\n", srcID, tgtID)
			fmt.Fprintf(buf, "          %s\n", w.Detail)
		case "DEAD":
			tgtID := ids[w.Target]
			fmt.Fprintf(buf, "DEAD      %s -> %s\n", srcID, tgtID)
			fmt.Fprintf(buf, "          %s\n", w.Detail)
		case "STALE":
			fmt.Fprintf(buf, "STALE     %s\n", srcID)
			fmt.Fprintf(buf, "          %s\n", w.Detail)
		}
	}
	buf.WriteString("\n")
}
