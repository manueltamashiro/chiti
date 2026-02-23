"use client";

import { useState, useMemo } from "react";
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getFilteredRowModel,
  flexRender,
  ColumnDef,
  SortingState,
} from "@tanstack/react-table";

type TableData = { columns: string[]; rows: unknown[][] };

type Props = { content: TableData | unknown };

export function TableBlock({ content }: Props) {
  const raw = content as TableData;
  const [sorting, setSorting] = useState<SortingState>([]);
  const [globalFilter, setGlobalFilter] = useState("");

  const columns = useMemo<ColumnDef<unknown[]>[]>(
    () =>
      (raw?.columns ?? []).map((col, i) => ({
        id: col,
        accessorFn: (row: unknown[]) => row[i],
        header: col,
        cell: (info) => String(info.getValue() ?? ""),
      })),
    [raw?.columns]
  );

  const table = useReactTable({
    data: raw?.rows ?? [],
    columns,
    state: { sorting, globalFilter },
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
  });

  if (!raw?.columns?.length) {
    return <p className="text-zinc-500 text-sm">No table data</p>;
  }

  return (
    <div className="my-2 overflow-auto rounded border border-zinc-700">
      <div className="px-3 py-2 border-b border-zinc-700">
        <input
          value={globalFilter}
          onChange={(e) => setGlobalFilter(e.target.value)}
          placeholder="Filter…"
          className="bg-zinc-800 text-zinc-200 text-xs px-2 py-1 rounded border border-zinc-600 w-48 outline-none"
        />
      </div>
      <table className="min-w-full text-xs text-zinc-300">
        <thead className="bg-zinc-800">
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id}>
              {hg.headers.map((h) => (
                <th
                  key={h.id}
                  onClick={h.column.getToggleSortingHandler()}
                  className="px-3 py-2 text-left font-medium text-zinc-400 cursor-pointer select-none whitespace-nowrap"
                >
                  {flexRender(h.column.columnDef.header, h.getContext())}
                  {h.column.getIsSorted() === "asc" ? " ↑" : h.column.getIsSorted() === "desc" ? " ↓" : ""}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr key={row.id} className="border-t border-zinc-700 hover:bg-zinc-800/50">
              {row.getVisibleCells().map((cell) => (
                <td key={cell.id} className="px-3 py-1.5 whitespace-nowrap">
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
