"use client";

import type { RuleMatch } from "@/lib/api";

interface TextHighlighterProps {
  text: string;
  matches: RuleMatch[];
}

interface Segment {
  text: string;
  match: RuleMatch | null;
}

function buildSegments(text: string, matches: RuleMatch[]): Segment[] {
  const sorted = [...matches]
    .filter((m) => Array.isArray(m.position) && m.position.length === 2)
    .sort((a, b) => a.position[0] - b.position[0]);

  const segments: Segment[] = [];
  let cursor = 0;

  for (const match of sorted) {
    const [start, end] = match.position;
    if (start < cursor || start >= end || end > text.length) continue;
    if (start > cursor) {
      segments.push({ text: text.slice(cursor, start), match: null });
    }
    segments.push({ text: text.slice(start, end), match });
    cursor = end;
  }

  if (cursor < text.length) {
    segments.push({ text: text.slice(cursor), match: null });
  }

  return segments;
}

export function TextHighlighter({ text, matches }: TextHighlighterProps) {
  const segments = buildSegments(text, matches);

  return (
    <div className="whitespace-pre-wrap break-words rounded-md border bg-muted/50 p-3 font-mono text-sm">
      {segments.length === 0 ? (
        <span className="text-muted-foreground">{text}</span>
      ) : (
        segments.map((segment, index) =>
          segment.match ? (
            <mark
              key={index}
              title={`${segment.match.rule_id} [${segment.match.position[0]}, ${segment.match.position[1]}]`}
              className="rounded-sm bg-yellow-200 text-yellow-950 dark:bg-yellow-800/60 dark:text-yellow-100"
            >
              {segment.text}
            </mark>
          ) : (
            <span key={index}>{segment.text}</span>
          )
        )
      )}
    </div>
  );
}
