// Ingestion (PDF text extraction / LLM output) flattens superscript unit
// exponents like "cm³" into plain "cm3" or "cm^3". Render them as real
// superscript characters instead of a literal digit or caret.
const SUPERSCRIPT_DIGITS: Record<string, string> = { '2': '²', '3': '³' };

const UNIT_EXPONENT_RE = /\b(km|cm|mm|nm|dm|m)\^?([23])(?![0-9a-zA-Z])/g;

export function formatUnits(text: string): string {
  if (!text) return text;
  return text.replace(UNIT_EXPONENT_RE, (_match, unit: string, digit: string) => `${unit}${SUPERSCRIPT_DIGITS[digit]}`);
}

// Ingestion sometimes leaves a "•"-delimited procedure/list inline in one
// paragraph instead of splitting it into separate Markdown list lines, so
// ReactMarkdown renders it as one run-on sentence. Break each "• " item
// onto its own line, with a blank line before the list so it doesn't get
// swallowed into the preceding sentence.
const BULLET_RE = /\s*[•‣▪]\s*/g;

export function formatBullets(text: string): string {
  if (!text || !/[•‣▪]/.test(text)) return text;
  return text
    .replace(BULLET_RE, '\n- ')
    .replace(/^\n- /, '- ')
    .replace(/([^\n])\n- /, '$1\n\n- '); // blank line before the list start only
}
