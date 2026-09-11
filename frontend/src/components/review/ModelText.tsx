import type { ReactNode } from 'react';

/**
 * Renders the two bits of Markdown a review model actually uses -- fenced code
 * blocks and inline backticks -- and nothing else.
 *
 * Not a Markdown library, on purpose. Model output is untrusted, and a full
 * renderer means a full parser's worth of surface for exactly the text an
 * attacker most wants to control: it arrives from a pull request they wrote.
 * Everything here is emitted as React text nodes, so it cannot become markup.
 */
export function ModelText({
  text,
  className = '',
}: {
  text: string;
  className?: string;
}) {
  return <div className={className}>{renderBlocks(text)}</div>;
}

const FENCE = /```([\w+-]*)\n([\s\S]*?)```/g;

function renderBlocks(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let key = 0;

  for (const match of text.matchAll(FENCE)) {
    const start = match.index ?? 0;
    if (start > last) out.push(<Paragraphs key={key++} text={text.slice(last, start)} />);
    out.push(
      <pre
        key={key++}
        className="my-2 overflow-x-auto rounded-md border border-line bg-ink px-3 py-2 font-mono text-xs leading-relaxed text-fg"
      >
        {match[1] && (
          <span className="mb-1 block text-[10px] uppercase text-dim">{match[1]}</span>
        )}
        {match[2]!.replace(/\n$/, '')}
      </pre>,
    );
    last = start + match[0].length;
  }
  if (last < text.length) out.push(<Paragraphs key={key++} text={text.slice(last)} />);
  return out;
}

function Paragraphs({ text }: { text: string }) {
  const paragraphs = text.split(/\n{2,}/).filter((p) => p.trim());
  return (
    <>
      {paragraphs.map((paragraph, i) => (
        <p key={i} className="whitespace-pre-wrap [&:not(:first-child)]:mt-2">
          {renderInline(paragraph.trim())}
        </p>
      ))}
    </>
  );
}

function renderInline(text: string): ReactNode[] {
  return text.split(/(`[^`\n]+`)/g).map((part, i) =>
    part.startsWith('`') && part.endsWith('`') && part.length > 2 ? (
      <code
        key={i}
        className="rounded bg-raised px-1 py-0.5 font-mono text-[0.85em] text-accent"
      >
        {part.slice(1, -1)}
      </code>
    ) : (
      <span key={i}>{part}</span>
    ),
  );
}
