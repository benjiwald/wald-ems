import { getState } from "@/lib/db";

export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  const encoder = new TextEncoder();
  let lastUpdated = "";
  let closed = false;

  const stream = new ReadableStream({
    start(controller) {
      function send(text: string) {
        if (closed) return;
        try {
          controller.enqueue(encoder.encode(text));
        } catch {
          closed = true;
          clearInterval(interval);
        }
      }

      const interval = setInterval(() => {
        if (closed) { clearInterval(interval); return; }
        try {
          const state = getState("site_state") as Record<string, unknown> & { updated_at?: string } | null;
          const updatedAt = state?.updated_at as string || "";

          if (updatedAt !== lastUpdated) {
            lastUpdated = updatedAt;
            send(`data: ${JSON.stringify(state)}\n\n`);
          }
        } catch {
          closed = true;
          clearInterval(interval);
          try { controller.close(); } catch { /* already closed */ }
        }
      }, 2000);

      send(": connected\n\n");

      // Close after 5 minutes (client should reconnect)
      setTimeout(() => {
        clearInterval(interval);
        if (!closed) {
          closed = true;
          try { controller.close(); } catch { /* already closed */ }
        }
      }, 5 * 60 * 1000);
    },
    cancel() {
      closed = true;
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      "Connection": "keep-alive",
    },
  });
}
