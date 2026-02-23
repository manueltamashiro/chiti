import { NextRequest } from "next/server";

const BACKEND = "http://127.0.0.1:8000";

export async function GET(_req: NextRequest) {
  const res = await fetch(`${BACKEND}/api/ws/token`);
  const data = await res.json();
  return Response.json(data, { status: res.status });
}
