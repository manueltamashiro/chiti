import { NextRequest } from "next/server";

const BACKEND = "http://127.0.0.1:8000";

export async function GET(req: NextRequest) {
  const path = req.nextUrl.pathname.replace("/api/memory", "/api/memory");
  const search = req.nextUrl.searchParams.toString();
  const url = `${BACKEND}${path}${search ? `?${search}` : ""}`;
  const res = await fetch(url);
  const data = await res.json();
  return Response.json(data, { status: res.status });
}

export async function DELETE(req: NextRequest) {
  const path = req.nextUrl.pathname.replace("/api/memory", "/api/memory");
  const res = await fetch(`${BACKEND}${path}`, { method: "DELETE" });
  const data = await res.json();
  return Response.json(data, { status: res.status });
}
