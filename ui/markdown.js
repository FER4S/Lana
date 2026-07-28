/* ─────────────────────────────────────────────────────────────────────────
   A markdown renderer for ONE known generator.

   This is not a general markdown parser and should not be made into one. It
   renders exactly the constructs PlanManager.render_markdown() emits — the
   draft banner blockquote, provenance and supplement tables, ## / ### heads,
   flag lists with indented `>` rule quotes, bold, inline code and rules.
   Scoping it that tightly is what makes it verifiable by reading it.

   SECURITY: a treatment plan contains model-generated prose, and this output
   is assigned to innerHTML. Every text fragment therefore goes through esc()
   BEFORE any markup is added. Block structure is detected on the raw line
   (escaping first would turn "> quote" into "&gt; quote" and break it), but
   no raw fragment ever reaches the output — see inline(), which is the only
   function that produces text nodes.
   ───────────────────────────────────────────────────────────────────────── */

window.LanaMarkdown = (function () {
  "use strict";

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /* Escape, THEN add emphasis. Escaping only touches & < > ", so the *, ** and
     ` markers survive it untouched. Bold runs before italic so ** is not eaten
     by the single-asterisk rule. */
  function inline(raw) {
    return esc(raw)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
  }

  /* Split a table row on unescaped pipes. PlanManager._cell() writes a literal
     pipe as \| , so a naive split() would tear a cell in half. */
  function splitCells(line) {
    const cells = [];
    let cur = "";
    for (let i = 0; i < line.length; i++) {
      const ch = line[i];
      if (ch === "\\" && line[i + 1] === "|") { cur += "|"; i++; continue; }
      if (ch === "|") { cells.push(cur); cur = ""; continue; }
      cur += ch;
    }
    cells.push(cur);
    if (cells.length && cells[0].trim() === "") cells.shift();
    if (cells.length && cells[cells.length - 1].trim() === "") cells.pop();
    return cells.map(function (c) { return c.trim(); });
  }

  function isSeparatorRow(line) {
    const t = (line || "").trim();
    if (!t.startsWith("|")) return false;
    const cells = splitCells(t);
    return cells.length > 0 && cells.every(function (c) {
      return /^:?-+:?$/.test(c);
    });
  }

  const RE_HR       = /^-{3,}$/;
  const RE_HEADING  = /^(#{1,6})\s+(.*)$/;
  const RE_BULLET   = /^[-*]\s+/;
  const RE_QUOTE    = /^>\s?/;
  const RE_SUBQUOTE = /^\s+>\s?/;
  const RE_FENCE    = /^```/;

  function startsNewBlock(line) {
    const t = line.trim();
    return !t || RE_HR.test(t) || RE_HEADING.test(t) || RE_FENCE.test(t) ||
           RE_BULLET.test(t) || RE_QUOTE.test(t) || t.startsWith("|");
  }

  function render(markdown) {
    const lines = String(markdown == null ? "" : markdown)
      .replace(/\r\n?/g, "\n")
      .split("\n");
    const out = [];
    let i = 0;

    while (i < lines.length) {
      const line = lines[i];
      const t = line.trim();

      if (!t) { i++; continue; }

      /* Fenced block — render_referral_memo() wraps the dictated case in one.
         Checked before the horizontal rule and everything else, because the
         contents are verbatim text and must not be parsed as markdown at all
         (a case containing "- " or "| " would otherwise become a list or a
         table). esc(), not inline(): no emphasis inside a code block. */
      if (RE_FENCE.test(t)) {
        i++;
        const body = [];
        while (i < lines.length && !RE_FENCE.test(lines[i].trim())) {
          body.push(lines[i]);
          i++;
        }
        if (i < lines.length) i++;   // consume the closing fence, if present
        out.push("<pre><code>" + esc(body.join("\n")) + "</code></pre>");
        continue;
      }

      if (RE_HR.test(t)) { out.push("<hr>"); i++; continue; }

      const heading = RE_HEADING.exec(t);
      if (heading) {
        // The document only uses # (inside the banner), ## and ###. Clamped so
        // a stray deeper heading can never emit an <h7>.
        const level = Math.min(heading[1].length, 3);
        out.push("<h" + level + ">" + inline(heading[2]) + "</h" + level + ">");
        i++; continue;
      }

      /* Table — only when the next line is a separator, so a lone line that
         happens to start with a pipe stays a paragraph. */
      if (t.startsWith("|") && isSeparatorRow(lines[i + 1])) {
        const header = splitCells(t);
        i += 2;
        const rows = [];
        while (i < lines.length && lines[i].trim().startsWith("|")) {
          rows.push(splitCells(lines[i].trim()));
          i++;
        }
        const hasHeader = header.some(function (c) { return c !== ""; });
        let html = '<div class="doc-table-wrap"><table>';
        if (hasHeader) {
          html += "<thead><tr>" + header.map(function (c) {
            return "<th>" + inline(c) + "</th>";
          }).join("") + "</tr></thead>";
        }
        html += "<tbody>" + rows.map(function (row) {
          return "<tr>" + row.map(function (c) {
            return "<td>" + inline(c) + "</td>";
          }).join("") + "</tr>";
        }).join("") + "</tbody></table></div>";
        out.push(html);
        continue;
      }

      /* Blockquote — the draft banner and the unreviewed-rules note. Rendered
         by recursing, so the `#` heading inside the banner stays a heading. */
      if (RE_QUOTE.test(t)) {
        const inner = [];
        while (i < lines.length && RE_QUOTE.test(lines[i].trim())) {
          inner.push(lines[i].trim().replace(RE_QUOTE, ""));
          i++;
        }
        out.push("<blockquote>" + render(inner.join("\n")) + "</blockquote>");
        continue;
      }

      /* List, with the indented `> verbatim` rule quotes attached to the item
         they belong to rather than floating off as their own block. */
      if (RE_BULLET.test(t)) {
        const items = [];
        while (i < lines.length) {
          const raw = lines[i];
          const trimmed = raw.trim();
          if (RE_BULLET.test(trimmed)) {
            items.push({ text: trimmed.replace(RE_BULLET, ""), quotes: [] });
            i++;
          } else if (items.length && RE_SUBQUOTE.test(raw)) {
            items[items.length - 1].quotes.push(raw.replace(RE_SUBQUOTE, ""));
            i++;
          } else if (items.length && trimmed && /^\s/.test(raw)) {
            items[items.length - 1].text += " " + trimmed;   // lazy wrap
            i++;
          } else {
            break;
          }
        }
        out.push("<ul>" + items.map(function (item) {
          let html = "<li>" + inline(item.text);
          if (item.quotes.length) {
            html += "<blockquote>" + item.quotes.map(function (q) {
              return "<p>" + inline(q) + "</p>";
            }).join("") + "</blockquote>";
          }
          return html + "</li>";
        }).join("") + "</ul>");
        continue;
      }

      /* Paragraph — runs until a blank line or the start of another block. */
      const para = [t];
      i++;
      while (i < lines.length && !startsNewBlock(lines[i])) {
        para.push(lines[i].trim());
        i++;
      }
      out.push("<p>" + inline(para.join(" ")) + "</p>");
    }

    return out.join("");
  }

  return { render: render, escape: esc, inline: inline };
})();
