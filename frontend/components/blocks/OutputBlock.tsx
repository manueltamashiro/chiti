"use client";

import type { OutputBlock as OutputBlockType } from "@/lib/api";
import { TextBlock } from "./TextBlock";
import { ChartBlock } from "./ChartBlock";
import { TableBlock } from "./TableBlock";
import { CodeBlock } from "./CodeBlock";
import { MetricBlock } from "./MetricBlock";
import { ActionConfirmBlock } from "./ActionConfirmBlock";

type Props = { block: OutputBlockType };

export function OutputBlock({ block }: Props) {
  switch (block.type) {
    case "text":
      return <TextBlock content={String(block.content ?? "")} />;
    case "chart":
      return <ChartBlock content={block.content} metadata={block.metadata} />;
    case "table":
      return <TableBlock content={block.content} />;
    case "code":
      return <CodeBlock content={String(block.content ?? "")} metadata={block.metadata} />;
    case "metric":
      return <MetricBlock content={block.content} />;
    case "action_confirm":
      return <ActionConfirmBlock content={block.content} />;
    default:
      // Unknown block type — render raw JSON in a code block
      return (
        <CodeBlock
          content={JSON.stringify(block.content, null, 2)}
          metadata={{ lang: "json" }}
        />
      );
  }
}
