/**
 * Reading a roster file (N4).
 *
 * The parser exists to catch the mistakes a spreadsheet makes, before the API
 * sees them: a reordered column, a role somebody typed by hand, a header
 * somebody deleted. Each of those becomes one clear message here instead of
 * forty failed rows in a result table.
 */
import { describe, expect, it } from "vitest";

import { parseUserImportCsv } from "@/lib/userImportCsv";

describe("parseUserImportCsv", () => {
  it("reads columns by name, so reordering them is harmless", () => {
    // The failure this guards: positional parsing puts every full name in the
    // email field the first time somebody drags a column in a spreadsheet.
    const { rows, errors } = parseUserImportCsv(
      "role,email,full_name\nviewer,ada@example.com,Ada Lovelace\n",
    );

    expect(errors).toEqual([]);
    expect(rows[0].email).toBe("ada@example.com");
    expect(rows[0].full_name).toBe("Ada Lovelace");
    expect(rows[0].role).toBe("viewer");
  });

  it("takes the file the export writes, unedited", () => {
    const exported =
      "email,full_name,team_id,role,is_active,last_login_at\n" +
      "ada@example.com,Ada,11111111-1111-1111-1111-111111111111,developer,true,\n";

    const { rows, errors } = parseUserImportCsv(exported);

    expect(errors).toEqual([]);
    expect(rows).toHaveLength(1);
    expect(rows[0].team_id).toBe("11111111-1111-1111-1111-111111111111");
  });

  it("refuses a file with no email column rather than importing nothing quietly", () => {
    const { rows, errors } = parseUserImportCsv("name,role\nAda,viewer\n");

    expect(rows).toEqual([]);
    expect(errors).toEqual([{ line: 1, reason: "missing_email" }]);
  });

  it("names the line a bad role is on", () => {
    // The administrator fixes their own file rather than reading a result
    // table to discover a typo it already contains.
    const { rows, errors } = parseUserImportCsv(
      "email,role\nada@example.com,developer\ngrace@example.com,admin\n",
    );

    expect(rows).toHaveLength(1);
    expect(errors).toEqual([{ line: 3, reason: "unknown_role" }]);
  });

  it("counts lines the way an editor does, header included", () => {
    const { errors } = parseUserImportCsv("email\nada@example.com\n\n,\n");

    expect(errors[0].line).toBe(3);
  });

  it("keeps a comma inside a quoted name", () => {
    const { rows } = parseUserImportCsv(
      'email,full_name\nada@example.com,"Lovelace, Ada"\n',
    );

    expect(rows[0].full_name).toBe("Lovelace, Ada");
  });

  it("treats an empty optional cell as absent, not as an empty string", () => {
    // An empty string would be sent as a value and stored as a full name
    // nobody typed, and as a role the API does not know.
    const { rows } = parseUserImportCsv("email,full_name,role\nada@example.com,,\n");

    expect(rows[0].full_name).toBeNull();
    expect(rows[0].role).toBeNull();
  });

  it("returns nothing for an empty file rather than throwing", () => {
    expect(parseUserImportCsv("")).toEqual({ rows: [], errors: [] });
  });
});

describe("parseUserImportCsv, round-tripping what the export writes", () => {
  it("reads the file back after the byte-order mark the export adds", () => {
    // Left in place the mark becomes part of the first column name, and the
    // file the portal wrote a minute ago comes back as "no email column".
    const exported = "﻿email,full_name\nada@example.com,Ada\n";

    const { rows, errors } = parseUserImportCsv(exported);

    expect(errors).toEqual([]);
    expect(rows[0].email).toBe("ada@example.com");
  });

  it("keeps a row together when a quoted name contains a line break", () => {
    const { rows, errors } = parseUserImportCsv(
      'email,full_name\nada@example.com,"Ada\nLovelace"\ngrace@example.com,Grace\n',
    );

    expect(errors).toEqual([]);
    expect(rows).toHaveLength(2);
    expect(rows[0].full_name).toBe("Ada\nLovelace");
  });

  it("does not treat the apostrophe guard as part of the value", () => {
    // The export prefixes a formula-looking cell with an apostrophe so no
    // spreadsheet executes it. Re-importing must not stack a second one, and
    // the name that comes back is the one that went out.
    const { rows } = parseUserImportCsv("email,full_name\nada@example.com,'=1+1\n");

    expect(rows[0].full_name).toBe("'=1+1");
  });
});
