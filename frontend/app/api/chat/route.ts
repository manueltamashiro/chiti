import { NextRequest } from "next/server";

const BACKEND = "http://127.0.0.1:8000";

export async function POST(req: NextRequest) {
  const body = await req.text();
  const upstream = await fetch(`${BACKEND}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
  });

  // Stream the SSE response directly to the client
  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      "X-Accel-Buffering": "no",
    },
  });
}
