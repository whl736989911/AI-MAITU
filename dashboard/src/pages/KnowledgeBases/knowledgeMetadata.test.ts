import { describe, expect, it } from "vitest";

import { metadataValuesForSchema } from "./knowledgeMetadata";

describe("metadataValuesForSchema", () => {
  it("drops values whose field left the schema", () => {
    expect(
      metadataValuesForSchema({ drawing_no: "A-102", retired_field: "old" }, [
        { key: "drawing_no", label: "Drawing", type: "text" },
      ]),
    ).toEqual({ drawing_no: "A-102" });
  });
});
