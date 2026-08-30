// Converts the LaTeX a MathLive math-field produces into a plain math
// expression string (e.g. "\frac{3}{4}" -> "(3)/(4)", "x^{2}" -> "x^(2)")
// that the backend's sympy-based DSL marker can parse.
export function latexToPlain(latex: string): string {
  let s = latex;

  // Strip spacing/sizing commands that carry no mathematical meaning.
  s = s.replace(/\\left|\\right|\\,|\\;|\\!|\\ /g, '');
  s = s.replace(/\\times|\\cdot/g, '*');
  s = s.replace(/\\div/g, '/');

  // \frac{A}{B} -> (A)/(B), \sqrt{A} -> sqrt(A), \sqrt[n]{A} -> (A)^(1/(n))
  // Handled with a brace-matching scan since the arguments can themselves
  // contain nested braces/fractions.
  s = replaceBraceCommands(s);

  // Superscripts/subscripts: ^{...} -> ^(...), drop subscripts (not used
  // for the numeric/algebraic answers this field targets).
  s = replaceBracedSuffix(s, '^', '^');
  s = stripBracedSuffix(s, '_');

  s = s.replace(/\{/g, '(').replace(/\}/g, ')');
  s = s.replace(/\\/g, '');
  s = s.replace(/\s+/g, '');

  return s;
}

function findMatchingBrace(s: string, openIdx: number): number {
  let depth = 0;
  for (let i = openIdx; i < s.length; i++) {
    if (s[i] === '{') depth++;
    else if (s[i] === '}') {
      depth--;
      if (depth === 0) return i;
    }
  }
  return -1;
}

// Reads one LaTeX "argument" starting at index i: a {...} group, a single
// control sequence like \pi, or a single character. Returns the raw
// (unprocessed) argument text and the index just past it, or null if there's
// nothing to read.
function readArg(s: string, i: number): { raw: string; end: number } | null {
  if (i >= s.length) return null;
  if (s[i] === '{') {
    const end = findMatchingBrace(s, i);
    if (end === -1) return null;
    return { raw: s.slice(i + 1, end), end: end + 1 };
  }
  const ctrlMatch = s.slice(i).match(/^\\[a-zA-Z]+/);
  if (ctrlMatch) return { raw: ctrlMatch[0], end: i + ctrlMatch[0].length };
  return { raw: s[i], end: i + 1 };
}

function replaceBraceCommands(s: string): string {
  let out = '';
  let i = 0;
  while (i < s.length) {
    const fracMatch = s.slice(i).match(/^\\frac/);
    const sqrtMatch = s.slice(i).match(/^\\sqrt(\[[^\]]*\])?/);
    if (fracMatch) {
      const numArg = readArg(s, i + fracMatch[0].length);
      const denArg = numArg && readArg(s, numArg.end);
      if (numArg && denArg) {
        const num = replaceBraceCommands(numArg.raw);
        const den = replaceBraceCommands(denArg.raw);
        out += `((${num})/(${den}))`;
        i = denArg.end;
        continue;
      }
    }
    if (sqrtMatch) {
      const afterCmd = i + sqrtMatch[0].length;
      const arg = readArg(s, afterCmd);
      if (arg) {
        const inner = replaceBraceCommands(arg.raw);
        const rootMatch = sqrtMatch[1];
        if (rootMatch) {
          const n = rootMatch.slice(1, -1);
          out += `((${inner})^(1/(${n})))`;
        } else {
          out += `sqrt(${inner})`;
        }
        i = arg.end;
        continue;
      }
    }
    out += s[i];
    i++;
  }
  return out;
}

// Replaces `<marker>{...}` with `<replacement>(...)`, recursing into the braces.
function replaceBracedSuffix(s: string, marker: string, replacement: string): string {
  let out = '';
  let i = 0;
  while (i < s.length) {
    if (s[i] === marker && s[i + 1] === '{') {
      const end = findMatchingBrace(s, i + 1);
      if (end !== -1) {
        const inner = replaceBracedSuffix(s.slice(i + 2, end), marker, replacement);
        out += `${replacement}(${inner})`;
        i = end + 1;
        continue;
      }
    }
    out += s[i];
    i++;
  }
  return out;
}

// Drops `<marker>{...}` or `<marker>x` (single-char) suffixes entirely.
function stripBracedSuffix(s: string, marker: string): string {
  let out = '';
  let i = 0;
  while (i < s.length) {
    if (s[i] === marker) {
      if (s[i + 1] === '{') {
        const end = findMatchingBrace(s, i + 1);
        if (end !== -1) {
          i = end + 1;
          continue;
        }
      } else if (s[i + 1] !== undefined) {
        i += 2;
        continue;
      }
    }
    out += s[i];
    i++;
  }
  return out;
}
