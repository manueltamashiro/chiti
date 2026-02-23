import { NextRequest } from "next/server";

const BACKEND = "http://127.0.0.1:8000";

export async function POST(
  _req: NextRequest,
  { params }: { params: { id: string } }
) {
  const res = await fetch(`${BACKEND}/api/tool/approve/${params.id}`, {
    method: "POST",
  });
  const data = await res.json();
  return Response.json(data, { status: res.status });
}
