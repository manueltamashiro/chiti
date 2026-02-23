import { NextRequest } from "next/server";

const BACKEND = "http://127.0.0.1:8000";

export async function GET(req: NextRequest) {
  const path = req.nextUrl.pathname; // /api/jobs or /api/jobs/queue
  const search = req.nextUrl.searchParams.toString();
  const res = await fetch(`${BACKEND}${path}${search ? `?${search}` : ""}`);
  const data = await res.json();
  return Response.json(data, { status: res.status });
}
