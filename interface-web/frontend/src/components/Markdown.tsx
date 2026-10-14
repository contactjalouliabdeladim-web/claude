/**
 * Markdown limité et sûr : paragraphes, listes, liens http(s), code, blocs de code,
 * gras/italique. Aucun HTML n'est interprété : tout passe par des éléments React.
 */
import { Fragment, type ReactNode } from "react";

function safeHref(url: string): string | null {
  try {
    const u = new URL(url, location.href);
    return u.protocol === "http:" || u.protocol === "https:" || u.protocol === "mailto:" ? u.href : null;
  } catch {
    return null;
  }
}

function inline(text: string, keyBase: string): ReactNode[] {
  const out: ReactNode[] = [];
  // code `x`, lien [t](u), url nue, **gras**, *italique* / _italique_
  const re = /(`[^`\n]+`)|(\[[^\]\n]+\]\([^)\s]+\))|(https?:\/\/[^\s<>()]+[^\s<>().,;:!?'"])|(\*\*[^*\n]+\*\*)|(\*[^*\n]+\*|_[^_\n]+_)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const k = `${keyBase}-${i++}`;
    const tok = m[0];
    if (m[1]) out.push(<code key={k}>{tok.slice(1, -1)}</code>);
    else if (m[2]) {
      const mm = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(tok)!;
      const href = safeHref(mm[2]);
      out.push(
        href ? (
          <a key={k} href={href} target="_blank" rel="noopener noreferrer">
            {mm[1]}
          </a>
        ) : (
          mm[1]
        ),
      );
    } else if (m[3]) {
      const href = safeHref(tok);
      out.push(
        href ? (
          <a key={k} href={href} target="_blank" rel="noopener noreferrer">
            {tok}
          </a>
        ) : (
          tok
        ),
      );
    } else if (m[4]) out.push(<strong key={k}>{tok.slice(2, -2)}</strong>);
    else if (m[5]) out.push(<em key={k}>{tok.slice(1, -1)}</em>);
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function withBreaks(lines: string[], key: string): ReactNode[] {
  const out: ReactNode[] = [];
  lines.forEach((line, i) => {
    if (i) out.push(<br key={`${key}-br${i}`} />);
    out.push(<Fragment key={`${key}-l${i}`}>{inline(line, `${key}-${i}`)}</Fragment>);
  });
  return out;
}

export default function Markdown({ text }: { text: string }) {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let b = 0;
  while (i < lines.length) {
    const line = lines[i];
    const fence = /^\s*```(\S*)/.exec(line);
    if (fence) {
      const code: string[] = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i++]);
      i++;
      blocks.push(
        <pre key={b++} className="md-pre">
          <code>{code.join("\n")}</code>
        </pre>,
      );
      continue;
    }
    if (/^\s*$/.test(line)) {
      i++;
      continue;
    }
    const heading = /^\s{0,3}#{1,6}\s+(.*)$/.exec(line);
    if (heading) {
      blocks.push(
        <p key={b} className="md-heading">
          {inline(heading[1], `h${b++}`)}
        </p>,
      );
      i++;
      continue;
    }
    if (/^\s*([-*+]|\d+[.)])\s+/.test(line)) {
      const ordered = /^\s*\d+[.)]\s+/.test(line);
      const items: string[] = [];
      while (i < lines.length && /^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*([-*+]|\d+[.)])\s+/, ""));
        i++;
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) {
          items[items.length - 1] += " " + lines[i].trim();
          i++;
        }
      }
      const key = b++;
      const children = items.map((it, j) => <li key={j}>{inline(it, `li${key}-${j}`)}</li>);
      blocks.push(ordered ? <ol key={key}>{children}</ol> : <ul key={key}>{children}</ul>);
      continue;
    }
    const para: string[] = [];
    while (
      i < lines.length &&
      !/^\s*$/.test(lines[i]) &&
      !/^\s*```/.test(lines[i]) &&
      !/^\s*([-*+]|\d+[.)])\s+/.test(lines[i])
    ) {
      para.push(lines[i].replace(/^>\s?/, ""));
      i++;
    }
    const key = b++;
    blocks.push(<p key={key}>{withBreaks(para, `p${key}`)}</p>);
  }
  return <div className="md">{blocks}</div>;
}
