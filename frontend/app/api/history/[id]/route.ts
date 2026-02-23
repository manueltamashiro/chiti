import { NextRequest } from "next/server";

const BACKEND = "http://127.0.0.1:8000";

export async function GET(
  req: NextRequest,
  { params }: { params: { id: string } }
) {
  const res = await fetch(`${BACKEND}/api/history/${params.id}`);
  const data = await res.json();
  return Response.json(data, { status: res.status });
}
