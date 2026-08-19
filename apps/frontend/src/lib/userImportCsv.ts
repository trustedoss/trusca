// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Reading the roster CSV an administrator pastes or uploads (N4).
 *
 * Deliberately small and deliberately strict about one thing: a header row is
 * required, and columns are read by name. Positional parsing looks friendlier
 * until somebody reorders two columns in a spreadsheet and the import puts
 * every full name in the email field, which the API then rejects one row at a
 * time with no hint about why.
 *
 * The recognised columns are the ones the export writes, so a file that came
 * out of the portal goes back in unedited.
 */

export interface ParsedImportRow {
  email: string;
  full_name?: string | null;
  team_id?: string | null;
  role?: "team_admin" | "developer" | "viewer" | null;
  password?: string | null;
}

export interface ImportParseError {
  /** 1-based line in the file, counting the header, so it matches an editor. */
  line: number;
  reason: "missing_email" | "unknown_role" | "too_few_columns";
}

export interface ImportParseResult {
  rows: ParsedImportRow[];
  errors: ImportParseError[];
}

const ROLES = new Set(["team_admin", "developer", "viewer"]);

/** Split one CSV line, honouring double quotes around a field. */
function splitLine(line: string): string[] {
  const out: string[] = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < line.length; i += 1) {
    const ch = line[i];
    if (quoted) {
      if (ch === '"' && line[i + 1] === '"') {
        field += '"';
        i += 1;
      } else if (ch === '"') {
        quoted = false;
      } else {
        field += ch;
      }
      continue;
    }
    if (ch === '"') {
      quoted = true;
    } else if (ch === ",") {
      out.push(field);
      field = "";
    } else {
      field += ch;
    }
  }
  out.push(field);
  return out.map((value) => value.trim());
}

/**
 * Split into rows, keeping a newline that sits inside a quoted field.
 *
 * Splitting on every newline is what a small parser does first and is wrong
 * for exactly one case: a name containing a line break. That row would break
 * in two, and both halves would arrive at the API as addresses it cannot
 * parse, reported against line numbers that do not match the file.
 */
function splitRows(text: string): string[] {
  const rows: string[] = [];
  let current = "";
  let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (ch === '"') {
      quoted = !quoted;
      current += ch;
      continue;
    }
    if (!quoted && (ch === "\n" || ch === "\r")) {
      if (ch === "\r" && text[i + 1] === "\n") i += 1;
      rows.push(current);
      current = "";
      continue;
    }
    current += ch;
  }
  rows.push(current);
  return rows;
}

export function parseUserImportCsv(text: string): ImportParseResult {
  const lines = splitRows(
    // The export leads with a byte-order mark so Excel reads it as UTF-8.
    // Left in place it becomes part of the first column name, and the file the
    // portal just wrote comes back as "no email column".
    text.replace(/^\ufeff/, ""),
  )
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
  if (lines.length === 0) return { rows: [], errors: [] };

  const header = splitLine(lines[0]).map((h) => h.toLowerCase());
  const at = (name: string) => header.indexOf(name);
  const emailAt = at("email");
  if (emailAt < 0) {
    return { rows: [], errors: [{ line: 1, reason: "missing_email" }] };
  }
  const nameAt = at("full_name");
  const teamAt = at("team_id");
  const roleAt = at("role");
  const passwordAt = at("password");

  const rows: ParsedImportRow[] = [];
  const errors: ImportParseError[] = [];

  lines.slice(1).forEach((line, index) => {
    const lineNumber = index + 2;
    const cells = splitLine(line);
    const value = (position: number): string =>
      position >= 0 && position < cells.length ? cells[position] : "";

    const email = value(emailAt);
    if (email.length === 0) {
      errors.push({ line: lineNumber, reason: "missing_email" });
      return;
    }
    const role = value(roleAt);
    if (role.length > 0 && !ROLES.has(role)) {
      // Refused here rather than sent on. The API would refuse it too, but as
      // one failed row among many, and the administrator would be reading a
      // result table to discover a typo their own file already contains.
      errors.push({ line: lineNumber, reason: "unknown_role" });
      return;
    }

    rows.push({
      email,
      full_name: value(nameAt) || null,
      team_id: value(teamAt) || null,
      role: (role || null) as ParsedImportRow["role"],
      password: value(passwordAt) || null,
    });
  });

  return { rows, errors };
}
