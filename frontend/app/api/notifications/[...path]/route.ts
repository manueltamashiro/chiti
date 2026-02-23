import { NextRequest } from "next/server";

const BACKEND = "http://127.0.0.1:8000";

function buildBackendUrl(req: NextRequest): string {
  const path = req.nextUrl.pathname;
  const search = req.nextUrl.searchParams.toString();
  return `${BACKEND}${path}${search ? `?${search}` : ""}`;
}

export async function GET(req: NextRequest) {
  const res = await fetch(buildBackendUrl(req));
  const data = await res.json();
  return Response.json(data, { status: res.status });
}

export async function POST(req: NextRequest) {
  const res = await fetch(buildBackendUrl(req), { method: "POST" });
  const data = await res.json();
  return Response.json(data, { status: res.status });
}
