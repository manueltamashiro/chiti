import { NextRequest } from "next/server";

const BACKEND = "http://127.0.0.1:8000";

export async function GET(req: NextRequest) {
  const search = req.nextUrl.searchParams.toString();
  const url = `${BACKEND}/api/history${search ? `?${search}` : ""}`;
  const res = await fetch(url);
  const data = await res.json();
  return Response.json(data, { status: res.status });
}
