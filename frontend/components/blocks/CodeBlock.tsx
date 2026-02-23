"use client";

import dynamic from "next/dynamic";

// Monaco is heavy — load it only on the client side
const MonacoEditor = dynamic(() => import("@monaco-editor/react"), { ssr: false });

type Props = {
  content: string;
  metadata?: Record<string, string>;
};

export function CodeBlock({ content, metadata }: Props) {
  const language = metadata?.lang ?? metadata?.language ?? "plaintext";

  return (
    <div className="my-2 rounded overflow-hidden border border-zinc-700">
      <div className="flex items-center justify-between px-3 py-1 bg-zinc-800 border-b border-zinc-700">
        <span className="text-xs text-zinc-400 font-mono">{language}</span>
        <button
          onClick={() => navigator.clipboard.writeText(String(content))}
          className="text-xs text-zinc-500 hover:text-zinc-300 transition-colors"
        >
          copy
        </button>
      </div>
      <MonacoEditor
        height={Math.min(Math.max(String(content).split("\n").length * 19, 80), 400)}
        language={language}
        value={String(content)}
        theme="vs-dark"
        options={{
          readOnly: true,
          minimap: { enabled: false },
          scrollBeyondLastLine: false,
          lineNumbers: "on",
          wordWrap: "on",
          fontSize: 13,
          fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
          padding: { top: 8, bottom: 8 },
          scrollbar: { vertical: "auto", horizontal: "auto" },
        }}
      />
    </div>
  );
}
